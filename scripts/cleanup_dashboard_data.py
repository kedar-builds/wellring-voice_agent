#!/usr/bin/env python3
"""
cleanup_dashboard_data.py
=========================
One-off production data cleanup for the dashboard "latest conversation" fixes
(user-approved 2026-08-07). It performs three operations:

  1. RE-ATTRIBUTE  — move assessments owned by the orphaned "Test User"
     (b286e754-6603-4676-8438-2543f576a4a9) to the real dashboard profile
     "Mr. Sharma" (dfff211c-237b-4a53-a8c8-b26ce9576f37). Both rows shared
     phone +918421971145; phone lookups picked the newer Test User row, so
     every call since Jul 26 was logged under the wrong user_id.
  2. DEDUPE        — Bolna retries webhook delivery on HTTP 500, and each retry
     created a new assessment row for the SAME call. For every bolna_call_id
     with more than one row (excluding NULL and the shared 'bolna_missed_call'
     fallback id used by unanswered-call tests), keep the earliest row and
     delete the rest.
  3. RE-ANALYZE    — recent rows (last 14 days) that have a transcript but an
     empty symptoms list were scored from a post-hoc Gemini analysis that
     systematically under-extracted (e.g. "I have the fever" -> symptoms=[],
     LOW risk). Re-run the improved vocabulary-aware extraction on their
     stored transcripts and rewrite symptoms/severity/score/risk/etc.

Usage:
    python scripts/cleanup_dashboard_data.py            # dry-run (no writes, no Gemini)
    python scripts/cleanup_dashboard_data.py --apply    # apply re-attribute + dedupe
    python scripts/cleanup_dashboard_data.py --apply --reanalyze   # + re-run Gemini analysis

Requires DATABASE_URL in .env (Postgres) and GEMINI_API_KEY for --reanalyze.
"""
import argparse
import asyncio
import os
import sys

# Make the repo root importable when run as `python scripts/cleanup_dashboard_data.py`
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Constants (from the live database — verified 2026-08-07)
# ---------------------------------------------------------------------------
TEST_USER_ID = "b286e754-6603-4676-8438-2543f576a4a9"     # "Test User" (clerk_id NULL)
SHARMA_USER_ID = "dfff211c-237b-4a53-a8c8-b26ce9576f37"    # "Mr. Sharma" (demo_sharma_001)
REANALYZE_DAYS = 14


def _reattribute(apply: bool) -> int:
    """Move Test User's assessments to Mr. Sharma. Returns affected count."""
    from src.database import get_pg_conn, _pg_cursor
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM assessments WHERE user_id = %s",
                (TEST_USER_ID,),
            )
            count = cur.fetchone()["n"]
            print(f"[re-attribute] Test User -> Mr. Sharma: {count} assessment(s) to move")
            if apply and count:
                cur.execute(
                    "UPDATE assessments SET user_id = %s WHERE user_id = %s",
                    (SHARMA_USER_ID, TEST_USER_ID),
                )
                print(f"[re-attribute] ✅ moved {cur.rowcount} row(s)")
            elif not apply:
                print("[re-attribute] dry-run — pass --apply to execute")
            return count


def _dedupe(apply: bool) -> int:
    """Delete duplicate assessment rows per bolna_call_id, keeping the earliest.
    Returns the number of rows that WOULD be / WERE deleted."""
    from src.database import get_pg_conn, _pg_cursor
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(
                """
                SELECT a.assessment_id, a.bolna_call_id, a.assessed_at
                FROM assessments a
                JOIN (
                    SELECT bolna_call_id, MIN(assessed_at) AS keep_ts
                    FROM assessments
                    WHERE bolna_call_id IS NOT NULL
                      AND bolna_call_id <> 'bolna_missed_call'
                    GROUP BY bolna_call_id
                    HAVING COUNT(*) > 1
                ) d ON d.bolna_call_id = a.bolna_call_id
                WHERE a.assessed_at <> d.keep_ts
                ORDER BY a.bolna_call_id, a.assessed_at
                """
            )
            dupes = [dict(r) for r in cur.fetchall()]
            print(f"[dedupe] {len(dupes)} duplicate row(s) across "
                  f"{len({d['bolna_call_id'] for d in dupes})} call(s):")
            for d in dupes[:12]:
                print(f"  - {d['bolna_call_id']} @ {d['assessed_at']} ({d['assessment_id']})")
            if len(dupes) > 12:
                print(f"  ... and {len(dupes) - 12} more")
            if apply and dupes:
                ids = [d["assessment_id"] for d in dupes]
                cur.execute(
                    "DELETE FROM assessments WHERE assessment_id = ANY(%s::uuid[])",
                    (ids,),
                )
                print(f"[dedupe] ✅ deleted {cur.rowcount} duplicate row(s)")
            elif not apply:
                print("[dedupe] dry-run — pass --apply to execute")
            return len(dupes)


def _reanalyze(apply: bool) -> None:
    """Re-run the improved extraction + scoring on recent empty-symptom rows."""
    from src.database import get_pg_conn, _pg_cursor
    from src.main import analyze_transcript_for_health_issues, dedupe_transcript
    from src.scoring_engine import calculate_score, determine_action, SYMPTOM_WEIGHTS

    import datetime
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=REANALYZE_DAYS)
    with get_pg_conn() as conn:
        with _pg_cursor(conn) as cur:
            cur.execute(
                """
                SELECT assessment_id, transcript, symptoms, severity, score, risk_level
                FROM assessments
                WHERE transcript IS NOT NULL
                  AND length(transcript) > 20
                  AND assessed_at >= %(cutoff)s
                  AND (symptoms IS NULL OR cardinality(symptoms) = 0)
                ORDER BY assessed_at DESC
                """,
                {"cutoff": cutoff},
            )
            rows = [dict(r) for r in cur.fetchall()]
            print(f"[reanalyze] {len(rows)} recent row(s) with empty symptoms + transcript")
            if not apply:
                print("[reanalyze] dry-run — pass --apply --reanalyze to execute (uses Gemini)")
                return

            async def _run():
                changed = 0
                for i, row in enumerate(rows, 1):
                    txn = dedupe_transcript(row["transcript"] or "")
                    analysis = await analyze_transcript_for_health_issues(txn)
                    symptoms = [s for s in analysis.get("symptoms", []) if s in SYMPTOM_WEIGHTS]
                    severity = (analysis.get("severity") or "low").lower().strip()
                    if severity not in {"low", "medium", "high", "critical"}:
                        severity = "medium"
                    score_result = calculate_score(symptoms, severity, confidence=1.0)
                    alert_result = determine_action(score_result["score"], 1.0)

                    cur.execute(
                        """
                        UPDATE assessments SET
                            symptoms = %(symptoms)s,
                            severity = %(severity)s,
                            base_score = %(base_score)s,
                            score = %(score)s,
                            risk_level = %(risk_level)s,
                            category = %(category)s,
                            action = %(action)s,
                            message = %(message)s,
                            steps = %(steps)s,
                            breakdown = %(breakdown)s,
                            transcript = %(transcript)s
                        WHERE assessment_id = %(assessment_id)s
                        """,
                        {
                            "symptoms": symptoms,
                            "severity": severity,
                            "base_score": score_result["base_score"],
                            "score": score_result["score"],
                            "risk_level": score_result["risk_level"],
                            "category": score_result["category"],
                            "action": alert_result["action"],
                            "message": alert_result["message"],
                            "steps": alert_result["steps"],
                            "breakdown": score_result["breakdown"],
                            "transcript": txn,
                            "assessment_id": row["assessment_id"],
                        },
                    )
                    if cur.rowcount:
                        changed += 1
                        print(
                            f"  [{i}/{len(rows)}] {str(row['assessment_id'])[:8]} "
                            f"{row['risk_level']}/{row['score']} -> "
                            f"{score_result['risk_level']}/{score_result['score']} "
                            f"symptoms={symptoms} severity={severity}"
                        )
                return changed

            changed = asyncio.run(_run())
            print(f"[reanalyze] ✅ updated {changed} row(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up dashboard assessment data")
    parser.add_argument("--apply", action="store_true", help="execute mutations (default: dry-run)")
    parser.add_argument("--reanalyze", action="store_true", help="also re-run Gemini analysis on recent rows")
    args = parser.parse_args()

    print(f"Running {'APPLY' if args.apply else 'DRY-RUN'} mode")
    moved = _reattribute(apply=args.apply)
    dupes = _dedupe(apply=args.apply)
    _reanalyze(apply=args.apply and args.reanalyze)
    print(f"\nDone. Dry-run shows {moved} row(s) to re-attribute, {dupes} duplicate(s) to delete.")
    if args.apply:
        print("Mutations applied — verify on the dashboard.")
    else:
        print("Nothing was written. Re-run with --apply (and --reanalyze) to execute.")


if __name__ == "__main__":
    sys.exit(main())
