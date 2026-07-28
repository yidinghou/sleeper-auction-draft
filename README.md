This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

## Sleeper projections export

Export player projections to a CSV:

```bash
npm run export:projections            # season 2026, weeks 1-3 (defaults)
npm run export:projections 2026 3     # season, week-count overrides
```

Output: `data/projections-<season>.csv` with columns:

```
player, position, team, sleeper_rank, bye_week, sleeper_proj_dollar,
season_pts_half_ppr, week1_pts_half_ppr, … weekN_pts_half_ppr
```

Points are **half-PPR**. Rows are sorted by `sleeper_rank` so the top of the file
mirrors the draft board.

### Data sources

| Columns | Source |
| --- | --- |
| `player`, `position`, `team`, `season_*`, `week*_*` | Sleeper API, via `lib/sleeper.ts` (`fetchSleeperFantasyPlayers`, `fetchSleeperProjections`, `fetchSleeperWeeklyProjections`) |
| `sleeper_rank`, `bye_week`, `sleeper_proj_dollar` | `data/sleeper-board-<season>.csv` (keyed by `player_id`), joined by `loadBoard()` in the export script |

If `data/sleeper-board-<season>.csv` is missing, the export still runs — those three
columns are just left blank.

### Refreshing the board file (rank / bye / $PROJ)

**`$PROJ` is not served by any Sleeper API.** The projections feeds only return points
(`pts_*`) and ADP (`adp_*`); Sleeper computes the auction dollar value in the browser on
the auction draft board. So the board columns have to be scraped from the **logged-in**
board with [Claude in Chrome](https://claude.com/claude-code). Ask Claude to refresh it —
the procedure is:

1. Connect the extension with `/chrome` and grant permission for `sleeper.com`.
2. Open the auction draft board while logged in, e.g. `https://sleeper.com/draft/nfl/<draft_id>`.
3. Read the player array out of the player-list React fiber (`.player-rank-list` →
   `memoizedProps`/state `items`, ~3k entries) to map `rank → player_id`.
4. Scroll-capture the virtualized list — drive `.player-rank-list .scrollbar-container`
   `scrollTop` in ~1800px steps, and on each step parse the rendered rows' text
   (`rank, name, pos, team, $PROJ, bye, pts`). Fill any missing ranks with a finer pass.
5. Join on `rank` and write `player_id,sleeper_rank,bye_week,sleeper_proj_dollar` to
   `data/sleeper-board-<season>.csv`.

Then re-run `npm run export:projections`. The board's `RK` column equals the API
projections order (`order_by=adp_2qb`), which is why the export sorts by `sleeper_rank`.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
