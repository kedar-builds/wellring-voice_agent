# AI Workflow Rules

## Approach

Wellring is built incrementally against the context files in this folder. Context files define what to build and the current state of progress — implement against them, don't infer or invent behavior from scratch. This project has a documented history of scope drift, silently-rewritten tests, and unverified "done" claims. The rules below exist specifically because of that history — treat them as non-negotiable, not as general best-practice suggestions.

## Scoping Rules

- Work on one feature unit, one bug, or one security fix at a time.
- Do not combine unrelated changes in a single commit or diff — e.g. an auth fix and an unrelated storage-provider rename belong in separate commits even if they happen to touch the same file.
- Prefer small, verifiable increments over large speculative changes.

## When to Split Work

Split an implementation step if it combines:

- Security/auth changes and unrelated feature or refactor changes
- Database schema or data changes and application logic changes
- A fix to production-critical code and unrelated test changes — review them together if they must ship together, but keep the diff scoped enough to review both at a glance

If a change cannot be verified end to end with raw output in one sitting, the scope is too broad — split it.

## Handling Missing Requirements

- Do not invent product behavior not defined in the context files (e.g. do not silently decide a missing `intent` field should default rather than 422 — that is a product decision, not an implementation detail).
- If a requirement is ambiguous, resolve it in the relevant context file (usually `project-overview.md` or `architecture.md`) before implementing.
- If a requirement is missing, add it to Open Questions in `progress-tracker.md` before continuing, rather than guessing and moving on.

## Protected / High-Caution Files

- `src/main.py` auth functions (`get_api_key`, `get_api_key_lenient`) — any change here is security-relevant and must be checked against `architecture.md`'s Auth and Access Model before merging.
- `.env` and any file containing credentials — never `cat`, print, or paste contents into any output, log, or chat context. Read specific keys through tooling that confirms presence/length without displaying the value.
- `tests/` assertions that encode a security or validation requirement — do not modify without an explicit, documented decision in `progress-tracker.md`.

## Hard Rules (non-negotiable — established after a prior incident)

1. **No direct pushes to `main`.** All changes go through a branch and a PR, reviewed by a human before merge.
2. **No destructive SQL** (DELETE, TRUNCATE, DROP, schema-altering DDL) **runs against production without a confirmed, recent backup and explicit human sign-off** — no matter how routine it seems.
3. **No credential is ever pasted, printed, or echoed into a chat, log, or terminal transcript.** Pipe secret-reading commands through redaction, or read values only via tooling that doesn't display them (length/prefix checks, not full values).
4. **Every claim of "done," "fixed," or "verified" includes the raw evidence in the same turn** — actual command output, an API response body, a byte comparison — never a paraphrase or self-report.
5. **A failing test is never resolved by loosening or renaming its assertion** without an explicit, logged product decision.

## Keeping Docs in Sync

Update the relevant context file whenever implementation changes:

- System architecture, boundaries, or invariants → `architecture.md`
- Storage model or data-handling decisions → `architecture.md`
- Code conventions → `code-standards.md`
- Feature scope → `project-overview.md`
- Anything at all → `progress-tracker.md`, every session

If a canonical version of these files ever exists in more than one location (e.g. a local copy and a repo copy), reconcile them immediately. Divergence here has previously caused already-disproven theories to resurface as if new.

## Before Moving to the Next Unit

1. The current unit works end to end within its defined scope, demonstrated with raw output.
2. No invariant defined in `architecture.md` was violated.
3. `progress-tracker.md` reflects the completed work, with evidence — not just a status label.
4. `pytest` passes (the full suite, not just the new/changed test).
5. If the change touched auth, secrets, or a data-destructive operation, the relevant Hard Rule above was followed — never skipped for expediency.
