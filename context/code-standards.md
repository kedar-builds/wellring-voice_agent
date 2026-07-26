# Code Standards

## General

- Fix root causes, not symptoms. Do not patch a test's expected value to match unwanted behavior — if behavior needs to change, change it deliberately and record the decision in `progress-tracker.md`.
- Keep modules single-purpose. Auth, DB access, storage, and assessment scoring stay in separate concerns, even when colocated in fewer files than ideal.
- Any claim that something "works" or "is fixed" must be backed by raw command or tool output in the same turn — not a summary, not a self-report, not "should be fine."

## Python / FastAPI

- All external, untrusted input (Bolna payloads, webhook bodies) is validated through Pydantic models before any business logic runs.
- No bare `except:` blocks that swallow errors silently. Log with enough context to debug, and never report a caught exception as a success.
- Type hints are required on new or modified function signatures. Avoid untyped `dict`/`Any` passthrough on anything crossing a system boundary (API in/out, DB read/write).
- Route handlers stay focused: validation, auth, and the actual operation are distinguishable steps, not interleaved.

## Secrets and Credentials

- No credential, key, or token literal ever appears in source code, test fixtures, or example files — environment variables only.
- No credential value is ever printed, logged, or included in a commit message, PR description, or shell command shown in agent/tool output. Treat anything that touches a terminal transcript or chat log as effectively public the moment it appears there.
- A credential known to have been exposed anywhere (chat, log, terminal output, a committed file) is treated as compromised immediately — rotate it — regardless of whether it "was just local dev."

## API Routes

- Validate and parse request input before any logic runs.
- Enforce auth before any mutation or data return. No lenient/bypass paths beyond the single documented Bolna placeholder exception in `architecture.md`.
- Return consistent, predictable response shapes. Errors carry enough detail to debug without leaking internals.

## Data and Storage

- Structured records (users, assessments, conversations) belong in PostgreSQL. Never treat a SQLite fallback as acceptable in a deployed environment — if `DATABASE_URL` is unset in production, that's a bug to fix, not a state to build around.
- Large binary content (recordings) belongs in Backblaze B2, referenced by URL — never stored inline in Postgres.

## Testing

- A test that encodes a security or validation requirement (e.g. "missing intent returns 422", "invalid API key returns 401") is never edited to match a regression. If code changes cause it to fail, that's a signal to investigate the code change — not to silence the test.
- The full test suite must pass before any unit of work is considered complete. A red test is resolved by fixing the underlying behavior, never by renaming or loosening its assertion.

## File Organization

- `src/` — application code (API, DB, storage, business logic)
- `tests/` — pytest suite; `conftest.py` for shared fixtures
- `context/` — AI-agent-facing specs and progress state (this folder)
- Root-level one-off scripts (migration helpers, rotation scripts) are temporary — delete them after use, and never leave one on disk containing a live credential
