# Contributing to WellRing Voice Agent

This document defines the development process for WellRing.
It exists because this project experienced data loss and fabricated verification in earlier
sessions. These rules prevent that from happening again.

---

## Hard Rules (non-negotiable)

1. **No direct commits to `main`.** All changes go through a branch and a diff you read
   personally before merging.

2. **No schema or data mutation without a fresh, confirmed backup.**
   Before running any DDL (`ALTER TABLE`, `DROP`, `TRUNCATE`) or destructive DML against
   production, you must:
   - Confirm a recent `pg_dump` exists and contains real data (open the file, count rows)
   - Paste the exact SQL statement you intend to run and get explicit sign-off

3. **Every "done" claim requires raw output, not a narrated summary.**
   Paste the actual query result, test output, or screenshot — never accept
   "it worked" without evidence.

4. **Keep the CHANGELOG updated.** Every merged PR adds an entry under `[Unreleased]`
   with what changed and what was verified.

---

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feat/<short-description>
# or: fix/<short-description>, chore/<short-description>
```

Branch naming convention:
- `feat/` — new feature
- `fix/` — bug fix
- `chore/` — maintenance (deps, docs, config)
- `security/` — security hardening

### 2. Make Changes

- Keep diffs focused and reviewable. One logical change per PR.
- Do not bundle refactors with features.
- Add or update tests for any logic change.

### 3. Run Tests Locally

```bash
# Activate the project venv
source venv/bin/activate

# Run the full suite
pytest tests/ -v

# All 41 tests must pass before opening a PR.
```

The test suite uses an in-memory SQLite database.
`DATABASE_URL` is unset in the test environment — do not add Postgres tests
that require a live connection.

### 4. Self-Review Your Diff

```bash
git diff main..HEAD
```

Read every changed line. Verify:
- No secrets or credentials are present (API keys, tokens, passwords)
- No `.env` file is staged (it is gitignored — keep it that way)
- No hardcoded auth bypasses
- Schema changes have a corresponding migration in `_migrate_sqlite_schema`

### 5. Open a Pull Request

PR description must include:
- **What changed** — 1–3 sentences
- **Why** — the motivation / issue it fixes
- **Verification** — paste raw test output or a curl result proving it works
- **Checklist:**
  - [ ] Tests pass (paste `pytest` output)
  - [ ] No secrets in diff
  - [ ] CHANGELOG updated

### 6. Merge

Only merge after:
- The full test suite is green (paste the output)
- You have read the diff yourself

---

## Environment Setup

```bash
# Clone
git clone <repo-url>
cd wellring-voice_agent

# Create and activate venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with real values — never commit .env
```

### Required Environment Variables

See `.env.example` for the full list. Minimum for local dev:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | LLM for assessment scoring |
| `OPENROUTER_API_KEY` | Nemotron 70B watchdog |
| `WELLRING_API_KEY` | API auth token |

For production (Railway), also set:
`DATABASE_URL`, `BOLNA_API_KEY`, `BOLNA_AGENT_ID`,
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_PHONE`,
`AISENSY_API_KEY`, `AISENSY_TEMPLATE`

---

## Database Guidelines

- **Local/tests:** SQLite (automatic, no config needed)
- **Production:** PostgreSQL via `DATABASE_URL` on Railway
- **Schema changes:** must be backward-compatible (ADD COLUMN only; no DROP without migration)
- Always add new columns to `_migrate_sqlite_schema` in `src/database.py` so existing DBs
  are upgraded automatically

### Migration Checklist for New Columns

1. Add the column to the `CREATE TABLE IF NOT EXISTS` DDL in `init_db()`
2. Add the column to `needed_cols` in `_migrate_sqlite_schema()`
3. Add the column to the Postgres `_add_columns_if_missing` block in `_init_postgres()`
4. Update `.env.example` if a new env var is introduced
5. Add a CHANGELOG entry

---

## Security Policy

- **Secrets:** Never hardcode API keys or passwords. Always use `os.environ.get()`.
- **Auth bypass:** Zero tolerance. No hardcoded tokens in source.
- **Git history:** Before pushing, run:
  ```bash
  git log --all -p | grep -E "^\+(.*API_KEY|.*TOKEN|.*PASSWORD)\s*=\s*[^'\"{$]"
  ```
  Any real key found must be rotated immediately, then the commit amended or history
  cleaned before the branch is pushed.
- **`.env` hygiene:** Confirm `.env` is gitignored before every push:
  ```bash
  git check-ignore -v .env
  ```

---

## Phase Gate: MVP Criteria

Do not declare MVP until every item is independently verified (not narrated):

- [ ] Data integrity — no unexplained gaps in `assessments` / `conversations`
- [ ] Identity resolution — deterministic under duplicate-phone conditions (tested)
- [ ] Assessment pipeline — correct scores across LOW / MEDIUM / HIGH / CRITICAL
- [ ] WhatsApp alerts fire automatically on HIGH/CRITICAL without manual step
- [ ] Railway deploy pipeline confirmed working (watch a real deploy complete)
- [ ] No hardcoded auth bypass, no secrets in code or plaintext logs
- [ ] Git history clean — no unreviewed commits on `main`
- [ ] Four-scenario smoke test passed against live production endpoint
