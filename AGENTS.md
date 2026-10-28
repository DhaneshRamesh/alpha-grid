# Repository context

Start with `docs/context/REPO_MAP.md` for architecture, entry points, contracts,
validation commands and dated research status. `docs/context/DECISIONS.md` holds
concise evidence-backed decision summaries and open questions.

These documents are navigation aids. Read the relevant implementation and tests
before changing behavior; dated documentation is not evidence of current remote
services or of ignored local datasets.

Run `powershell -NoProfile -File tools/repo-context.ps1 -Check` to detect changes
against `docs/context/merkle.json`. A stale index is a prompt to inspect changed
files and revise affected summaries. Refresh it with
`powershell -NoProfile -File tools/repo-context.ps1` after updating context.
Stage new source files or add them to the script's explicit support list before
refreshing so Git includes them in the inventory.

Record facts, assumptions, decisions and verification results in context notes;
do not record private internal chain-of-thought or credentials.
