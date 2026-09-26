# MLB All-Star Aggregator (2024-2026)

I scrape Baseball Reference (BR) and the MLB The Show 26 top-100 list (theshowratings.com/lists/top-100-players), build a CSV of the 2024-2026 All-Stars, and serve a local site over it. Every fetched page is committed under `data/raw/`, so only the install needs the network. WRITEUP.md explains my approach.

## Reviewer quickstart

You need `make` and Python 3.11+ (if `python3` is older, use `make install PYTHON=python3.11`). From the repo root:

```sh
make install
make scrape
make build
make serve
```

Then open http://localhost:8080; Ctrl-C stops the server. If 8080 is busy, run `make serve PORT=8081` and open that port. `make install` creates `.venv` with the pinned packages (about 7 s). `make scrape` sends no requests, because every page is cached (about 7 s). `make build` writes the CSV and `website/data.js` (about 8 s). Times are from my machine. The 274 cached pages take 166 MB on disk; the clone downloads about 10 MB. To re-fetch every page, run `.venv/bin/python scrape.py --force`: 274 requests at 4 seconds each, about 18 minutes. `make test` runs the tests (about 25 s, no network) and `make lint` runs ruff.

## All-Star counts per season

The spec asks for "how many All-Star rows you found per season" and says to "expect roughly 64-68 All-Stars per year." Those are two different measures, so I report both.

**Players** are the people selected as All-Stars. **Rows** are lines in the CSV: one per player, season, stat type (batting or pitching) and team, so one player can have more than one row.

| Season | All-Star players | Extra rows: both tables | Extra rows: traded | CSV rows |
|---|---|---|---|---|
| 2024 | 76 | 4 | 3 | 83 |
| 2025 | 81 | 6 | 2 | 89 |
| 2026 | 77 | 8 | 3 | 88 |

That's 260 rows for 177 players.

- **Both tables:** a player marked All-Star in both the batting and the pitching table gets a row for each, because the spec's rule counts an All-Star mark in either table. Examples: Shohei Ohtani (2025-26), relievers listed at 0 PA in the batting table (Kirby Yates 2024), and position players who pitched (Willi Castro 2024).
- **Traded:** an All-Star who played for two teams in a season gets one row per team, each with that team's stats. Rows are never summed. Examples: Luis Arraez 2024 (Miami, San Diego), Ryan O'Hearn 2025.

The player counts (76 to 81) are above the 64-68 estimate. They match the full rosters on BR's All-Star game pages exactly: every player marked All-Star on a team page is on a roster, and every roster player is marked on a team page. The spec's 64 matches two 32-player rosters, each league's size under MLB's current rules. BR's game pages list everyone named to the game, 76, 81 and 77 players, and 59, 63 and 61 of them appeared in it. I report BR's full list.

My row counts equal the Example output's (83, 89, 88), and all 260 rows pair one for one with its rows.

## Assumptions

- **Rows for players in both tables:** a player marked All-Star in both the batting and the pitching table gets a row in each, because the spec counts an All-Star mark in either table.
- **One row per team for trades:** a traded All-Star gets a row for each team with that team's stats, because team_id is part of the spec's grain and a sum would pair one team's record with another team's numbers.
- **BR's display name over the example's:** `full_name` keeps the name as BR prints it, because that's what the column asks for; the spec's normalized form is only for matching.
- **All-Star game rosters as the reconciliation source:** each season is checked against BR's All-Star game page, because it lists everyone selected that year and comes from the same site as the team pages.
- **A name collision stops the build:** if a Show name fits two All-Stars, or an All-Star's name fits two Show entries, the build stops, because a guess could give one player another's rating. None occur in this data.
- **Stat labels kept as the page has them:** each stat column uses the page's own label and `stat_type` says which table it came from, so every value maps straight back to a cell on the page.
- **scraped_at is the team page's fetch time:** the spec says when the row was built, but the fetch time says when the stats were captured and keeps builds byte-identical.
- **primary_position comes from the team page:** SP, CL and RP appear only there, so it can differ by season; profile_position keeps the player page's value.
- **Age is left out:** birth_date carries it, and a season-specific age would need a fixed reference date.
- **Empty Pos cells filled from the player's other row:** when a team page leaves a row's Pos blank, the build takes the Pos from the player's other row on that page, else P for a pitching row, because that other row is where the page names his position.

## Validation

`build.py` runs these checks and writes nothing if one fails:

- **Keys:** `(player_id, season_id, stat_type, team_id)` is unique across all 260 rows, and no row has a null `player_id`, `team_id` or `season_id`.
- **Every team-season has an All-Star:** 30 teams each season.
- **Allowed values:** `stat_type`, `bats` (R/L/S), `throws` (R/L), booleans, dates and numbers.
- **Selections:** `all_star_selections_2024_2026` equals the player's number of distinct seasons.
- **Rosters:** each season reconciles with the full roster on BR's All-Star game page. A gap would go to the appendix, `data/output/all_star_gaps.csv`, not fail the build. There are none, so it holds only its header.
- **Spot checks** against values I read by hand from the BR pages:

| Player | Why | CSV | BR page |
|---|---|---|---|
| Luis Arraez 2024 | traded | MIA: 33 G, .719 OPS. SDP: 117 G, .744 OPS | the same, on the Miami and San Diego team pages |
| Kirby Yates 2024 | reliever | pitching: 33 SV, 1.17 ERA, 85 SO | the same, on the Texas team page |
| Juan Soto 2024 | foreign-born | born in Santo Domingo, Dominican Republic | the same, on his player page |
| Aaron Judge 2024 | a full season | 58 HR, 1.159 OPS; Yankees 94-68 | the same, on the Yankees team page |

## MLB The Show match rate

79 of the 177 All-Stars (45%) are on the Show top 100: 42 of 76 in 2024, 47 of 81 in 2025, 35 of 77 in 2026. No join could do better: 21 of the list's 100 players weren't All-Stars in 2024-2026, so at most 79 can match. The 21, with their Show rank: Blake Snell (#12), Brandon Woodruff (#40), Gerrit Cole (#48), Framber Valdez (#52), Kevin Gausman (#54), Nick Pivetta (#56), George Kirby (#62), Geraldo Perdomo (#63), Nathan Eovaldi (#67), Seiya Suzuki (#69), Sonny Gray (#70), Brice Turang (#75), Luis Castillo (#81), Spencer Schwellenbach (#83), Jackson Chourio (#84), Nico Hoerner (#87), Willy Adames (#91), Cade Horton (#94), Dansby Swanson (#97), Devin Williams (#98), Gabriel Moreno (#99). None of the 21 shares even a last name with an All-Star. The example has the same 79 matches.

Names are compared after the spec's normalization, using the player page's display name. A match whose bats/throws disagree fails the build; none do.

## The site

`make serve` serves `website/`: plain HTML, CSS and JavaScript with no build step. `make build` writes the rows into `website/data.js`, so the page needs no fetch and also opens straight from the file. It has the example's filters (season, Show top 100), search by name, player ID, team or team code (case and accents don't matter), sorting on six columns with All-Star selections first by default, and a BR profile link on every row. The third Show filter reads "Not in top 100" rather than the example's "Not in Show top 100", so the filters fit one line on a phone.

## Data notes

- An empty cell means null. Booleans are lowercase `true`/`false`. The site shows "-" where a value is missing.
- The site's "as of" time is the latest team-page fetch time (`scraped_at`), in UTC.
- On pitching rows, the stat labels both tables share (G, R, H, HR, BB, SO, WAR, HBP, IBB) hold pitching values: HR there is home runs allowed.
- OAK (2024) and ATH (2025-26) are one franchise under BR's per-season codes.
- `full_name` comes from the player page, so the team tables' name marks (`*`, `#`, "(10-day IL)") never reach the CSV.
- Five rows have an empty Pos cell on the team page (Willi Castro 2024, Tanner Scott 2024 SDP, Zach McKinstry 2025, Foster Griffin 2026, Justin Verlander 2026). The Assumptions section above has the rule the build uses.
- Tests and counts are pinned to the committed cache. I fetched the 2026 pages on 2026-09-25, before the season ended, so a `--force` re-scrape can change 2026 values.

## Where the Example tab and BR disagree

Where the example and the cached pages differ, the CSV follows the pages.

Values:

- **OPS and OPS+ at 0 PA** (14 rows), which the page leaves blank: the example shows a dash on 9, 0.000 and -100 on 3, and real-looking rates on 2 (Ryan Helsley 2024, Josh Hader 2025).
- **WHIP:** Zach McKinstry's 2025 page shows 0 hits and 0 walks in one out, so 0.000; the example shows 1.50. Kyle Finnegan's 2024 page has 1.335, which is (61 H + 24 BB) / 63⅔ IP (BR shows 63.2); the example has 1.32.
- **Bats/throws:** the example shows B/R for Ozzie Albies 2026, where its other switch hitters show S (his page says Both, which the spec codes S), and nothing for Jurickson Profar 2024.
- **2025 OPS+:** 1 to 4 points off on 36 rows. OPS matches on all 36, so the difference is in the league and park figures OPS+ is scaled by.

Timing:

- **2026 records:** 24 teams are one game later on my pages (+12 wins, +12 losses), so 12 games were played between the two captures. 2026 stats differ for the same reason: Ozzie Albies has a .694 OPS and a 90 OPS+ on my page against .697 and 91 in the example, with the Braves at 93-66 against 93-65.

Display choices:

- **Pitching positions:** the example shows P or a dash on 67 rows where the Pos cell says SP, CL or RP.
- **Empty Pos cells:** the example shows P or a dash on all five, matching mine only on Tanner Scott's 2024 San Diego row.
- **Names:** on 40 rows (26 players) the example shows the matching form (Jose Ramirez, Bobby Witt). The CSV keeps BR's display name, as `full_name` asks.
- **Matthew Boyd:** BR lists boydma01 (2025, pitching, CHC) as "Matthew Boyd" on his team page, the All-Star game page and his own page; the example says "Matt Boyd". He isn't on the Show top 100, so the example's spelling comes from somewhere else. I kept BR's, and the rows still pair one for one.
- **St. Louis:** the example writes St Louis (7 rows).
- **Number format:** the site shows BR's own format ("OPS .977", "WHIP 0.984"), where the example shows "OPS 0.977" and "WHIP 0.98".
- **Codes:** the example shows TBD and ATH3 for BR's TBR and OAK.
- **Links:** the example links a BR search, not the player's profile.

## How the scrape stays polite

- One request every 4 seconds at most, retries included (15 a minute; BR blocks above 20).
- Retries only on server errors, timeouts and dropped connections, after 8 s and then 16 s.
- A 403 or 429 stops the run with no retry and writes `data/.scrape.blocked`. No run sends a request until someone deletes it.
- Any other non-200 (a redirect, a 404) stops the run too. A response that isn't a complete page of its kind goes to `data/quarantine/`, never the cache.
- A lock on `data/.scrape.lock` allows one scrape at a time. It also records the last request's start, so the spacing holds across runs.
- The scrape and the build check every cached page against its SHA-256 in `data/raw/manifest.json`. `.venv/bin/python scrape.py --check-cache` checks all 274.
- The User-Agent names the project.

## Layout

```
scrape.py     fetch into data/raw/ (--force, --offline, --check-cache)
build.py      parse, join, validate, write data/output/ and website/data.js
allstar/      fetcher, cache, a parser per page type, name matching, rows, checks
tests/        pytest on the cached pages and fake HTTP responses
website/      the static site
data/raw/     274 cached pages and manifest.json
```

## Known limits

- The Show join uses names only, as the spec asks, so a player listed under another name would be missed. I found none.
- Run one build at a time; there's no lock between builds.
