# Changelog

All notable changes to WellRing Voice Agent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

> **Ground-truth rule (Phase 7.4):** Every entry requires raw tool output as proof —
> never a narrated summary. Add the verification artifact path or paste output inline.

---

## [Unreleased]

### Added
- Inbound Twilio WhatsApp webhook: `POST /twilio-webhook` with `X-Twilio-Signature` validation,
  form-encoded body parsing, inbound-message logging, and a TwiML reply that replaces Twilio's
  canned "Standard auto-reply". 5 tests in `tests/test_twilio_webhook.py`.
  *Verification: `pytest -q` → 57 passed in 12.71s (2026-08-05)*
- `tests/test_history_sentinel_exclusion.py` — recreated from deleted source; 4 tests covering sentinel
  exclusion, orphan exclusion, real-user counting, and user-scoped isolation. All 4 pass.
  *Verification: `pytest tests/test_history_sentinel_exclusion.py -v` → 4 passed in 0.85s (2026-07-26)*
- `CHANGELOG.md` — this file (Phase 7.4)
- `CONTRIBUTING.md` — branch + review process documentation (Phase 7.1/7.3)
- Nemotron 70B watchdog integration: hallucination detection, confidence-based follow-ups,
  audit logging to `nemotron_audits` table (7 tests, all passing)
### Fixed
- `get_symptom_repeat_count` — excluded `is_system` sentinel user from global symptom
  counts to prevent score inflation (Phase 3.4); both Postgres and SQLite paths corrected
- `_migrate_sqlite_schema` — added `is_system INTEGER NOT NULL DEFAULT 0` to migration dict
  so legacy test DBs get the column automatically; resolves `no such column: u.is_system`
- `users` CREATE TABLE DDL — added `is_system` to schema so fresh DBs never miss the column
- `tests/conftest.py` — added session-scoped `_init_test_db` autouse fixture that patches
  `_use_postgres` → False and calls `init_db()` once per session, guaranteeing all tables
  exist for direct-DB tests (previously only the `client` fixture ran `init_db`)
- `test_nemotron_watchdog.py` — corrected mock targets to `src.main.trigger_alerts_if_needed`
  (the actual escalation path); removed stubs for non-existent `trigger_outbound_call` /
  `send_twilio_alert` symbols

### Removed
- `render.yaml` — Render is no longer the deployment target; Railway is the sole deploy
  platform (Phase 5.5)
- Hardcoded `X-API-Key: ***REMOVED***` auth bypass (Phase 6.1)

### Security
- Rotated Bolna API key (previously leaked as a hardcode fallback in git history —
  commit contains `bn-0d9f1aa2347d4aa68b593c8e0680aed5`; key has been invalidated)
- Rotated PostgreSQL password (Phase 6.3)
- CORS origins audited; stale `onrender.com` references removed (Phase 5.2)
- Verified git history for leaked credentials via `git log -G` regex search; confirmed no active external credentials exist in history (Phase 6.4)
- Stripped `wellring-secure-2026` from 7 JSON config snapshots and 2 helper scripts
  (`check_system.py`, `smoke_test.py`); scripts now fail loudly if `WELLRING_API_KEY` is
  unset rather than silently using the old bypass. Stale `onrender.com` URL in
  `book_appointment` webhook replaced with Railway URL across all config files. (2026-07-26)

---

## [0.9.0] — 2026-07-25

### Added
- Postgres-first data layer: `get_pg_conn`, `log_assessment_pg`, `upsert_health_history`,
  `log_conversation_turn`
- `nemotron_audits` table (SQLite + Postgres) for watchdog audit trail
- Session-scoped test DB initialisation in `conftest.py`
- `get_symptom_repeat_count` user_id-scoped filtering

### Fixed
- Deterministic `get_user_by_phone` using `ORDER BY created_at DESC LIMIT 1`
  (commit `c7a52b8`); prevents duplicate-row ambiguity
- `_ensure_anonymous_user_pg` — replaced epoch-timestamp hack with proper
  `is_system` boolean column and idempotent upsert
- `/assess` endpoint — `intent` field made required again after accidental revert

### Security
- All Railway environment variables audited; Render-targeted values removed
- `.env` confirmed absent from git history (only `.env.example` committed)

---

## [0.8.0] — 2026-07-19

### Added
- Initial Railway deployment configuration (`railway.toml` / `Dockerfile`)
- Automated `pg_dump` backup via GitHub Actions to B2 (Phase 1.2)
- Staging database isolation (Phase 1.3)

### Fixed
- `assessments` and `conversations` tables recreated after data-loss incident
  (Phase 0/1 root cause: unscoped DELETE with cascade from test harness)

### Security
- Rotated B2 Application Key (exposed in prior session log)

---

## [0.7.0] — 2026-07-18

### Added
- FastAPI backend: `/assess`, `/health`, `/symptoms`, `/risk-levels`, `/alerts`,
  `/reminders`, `/users` endpoints
- SQLite → Supabase → PostgreSQL backend priority chain
- Bolna v2 voice agent integration
- WhatsApp/Twilio alert pipeline triggered automatically on HIGH/CRITICAL assessments

### Known Issues
- `assessments` and `conversations` tables wiped (data loss — see Phase 0/1)
- Hardcoded auth bypass present (removed in 0.9.0)
