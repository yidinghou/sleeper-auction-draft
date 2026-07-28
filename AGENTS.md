<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Data procedures

## Projections CSV (`data/projections-<season>.csv`)

Regenerate with `npm run export:projections`. Name / position / team / points come
from the Sleeper API (`lib/sleeper.ts`).

But `sleeper_proj_dollar` (`$PROJ`), `sleeper_rank`, and `bye_week` are **NOT in any
Sleeper API** — `$PROJ` is computed in Sleeper's browser client on the auction draft
board. They must be scraped from the logged-in board with Claude in Chrome into
`data/sleeper-board-<season>.csv` (keyed by `player_id`), which the export joins.
Don't go hunting for a dollar/value endpoint — there isn't one. Full step-by-step is
in the README under "Sleeper projections export".
