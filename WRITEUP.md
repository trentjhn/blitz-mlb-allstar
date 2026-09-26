# Writeup

## Planning the scrape

Baseball Reference blocks traffic above 20 requests a minute, so the scraper waits 4 seconds between request starts, retries included: 15 a minute at most. It retries only server errors, timeouts and dropped connections. A 403 or 429 stops the run and writes a block file; no later run sends a request until someone deletes it.

The order: the Show top-100 list, then each season's league page (the run stops unless it lists exactly 30 teams), the 90 team pages, the three All-Star game pages, and last the player pages. Before fetching any profile, I checked the team parser's All-Star rows against the example tab. Each player is fetched once: 177 profiles.

Pages are cached in data/raw/, with each fetch time, size and SHA-256 in manifest.json. A cached URL is never requested again without --force. A response that isn't a complete page of its kind (large enough, ending in </html>, naming its own URL as canonical) goes to quarantine and stops the run, so a throttle notice can't become data. With the committed 274 pages, a normal run sends zero requests.

## Parsing and the data model

One row is a player, season, stat table and team. The spec counts an All-Star link in either the batting or the pitching table, so Shohei Ohtani gets both rows, and a traded All-Star gets one row per team with that team's stats, never a sum (Luis Arraez 2024 has a Miami row and a San Diego row). Stat columns keep the page's labels and stat_type names the table, so HR on a pitching row is home runs allowed.

IDs are Baseball Reference's own. Tables are found by exact id, which keeps the postseason tables out, and cells by their data-stat attribute. Every All-Star game roster ships inside an HTML comment, so the lookup tries the page first and the comments second. Names come from the player page, so the team tables' * and # marks and "(10-day IL)" never reach the CSV.

The Show join uses the spec's normalization. If a Show name fits two All-Stars, or an All-Star's name fits two Show entries, the build stops instead of guessing. A namesake could still take another player's rating, so a match whose bats and throws disagree fails the build. Neither happens: 79 of 177 All-Stars match, the same 79 as the example tab.

## Validation

build.py writes nothing unless every check passes: a unique key, no empty IDs, an All-Star on all 30 teams each season, allowed values, and selections equal to distinct seasons. Spot checks compare Arraez's two splits, Kirby Yates, Juan Soto and Aaron Judge (all 2024) against values I read off the pages. Each season is reconciled with its All-Star game roster: 76, 81 and 77 players, an exact match both ways. That's above the spec's "roughly 64-68", and I couldn't find where the estimate comes from, so the README reports players and rows separately. The rows (83, 89, 88) equal the example's.

## Bugs I hit

Five pitching rows have an empty position cell. The build takes the player's position from his row in the other table, else P, and fails a P on anyone whose profile doesn't say Pitcher. Parsing debut dates with strptime's %B depended on the machine's locale, so a fixed month list replaced it. A test showed that back-to-back scrapes could send requests 0.1 seconds apart, so the lock file now records the last request's start. And 2026 is in progress: 24 teams are a game further along on my pages than in the example, so 2026 stats move between scrapes while the rows stay fixed.

## The website

build.py writes the rows into website/data.js, so the site is plain HTML, CSS and JavaScript with no build step and no CSV parsing in the browser (birthplaces contain commas). make serve runs it on 127.0.0.1:8080. It has the example's season and Show filters, search, default sort, sortable columns and BR profile links. The look is my own.

## Tradeoffs and next steps

The Python is about 1,800 lines outside the tests, heavy for a scraper. Most of it guards the fetch and checks the output. With more time I'd add a build lock, since nothing stops two builds from overlapping, and re-scrape 2026 after the season ends.

In production I'd run the scrape on a schedule, keep raw pages by fetch date, load rows into Postgres, alert on any failed check, and match Show players through a stored ID map instead of names.
