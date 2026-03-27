# Research Handoff Docs

## What I changed

- Added a new local rolling context file at `RESEARCH.md`.
- Added `RESEARCH.md` to `.gitignore` so it can serve as local working memory without being committed by default.
- Updated `AGENTS.md` to tell future agents to read and maintain `RESEARCH.md`.
- Added `THREAD_PROMPT.md` as a copyable prompt for starting a follow-on thread on the main syscall task.

## Why this works

- `RESEARCH.md` centralizes the user's goals, preferences, validated findings, decisions, and next steps in one place.
- The `AGENTS.md` pointer makes new threads discover that context immediately instead of re-deriving it from chat history.
- Ignoring `RESEARCH.md` keeps it available locally while avoiding accidental commits of evolving working notes.
- `THREAD_PROMPT.md` gives a concrete handoff artifact that another thread can use directly.

## Alternatives considered

- Keep all rolling context in `AGENTS.md` only.
  - Rejected because `AGENTS.md` would become noisy and less maintainable.
- Reuse the existing lowercase `research.md`.
  - Rejected because that file already serves as a different report-style document, while the user asked for a new rolling context doc.
- Put the handoff prompt inside `AGENTS.md`.
  - Rejected because `AGENTS.md` should stay as a stable instruction entry point, while the thread prompt is task-specific.

## Pros

- Fast recovery of research context for future threads.
- Clear separation between stable repo instructions and rolling local notes.
- Explicit preservation of critical findings, especially the live proof that Redis object ordering changes across fresh runs.

## Cons

- `RESEARCH.md` is gitignored, so it will not be committed unless force-added.
- Future agents must actually follow `AGENTS.md` and keep `RESEARCH.md` updated, or the file will drift.
