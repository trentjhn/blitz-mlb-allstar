"""Build data/output/all_stars_2024_2026.csv from the cached pages, check it, and report.

It also writes website/data.js, the same rows for the site to load.

Reads data/raw/ only and sends no requests, and every page must match its manifest entry.
The CSV, the roster-gaps appendix and the site's data file are written only when every check
passes. All are written to temp files first, so a failed write replaces none; then each is
renamed into place.

    python build.py
"""

import csv
import hashlib
import io
import logging
import os
import sys
from pathlib import Path

from allstar import config
from allstar.cache import load_manifest, remove_partial_writes, stage
from allstar.discover import allstar_url, league_url, team_urls
from allstar.names import match_show
from allstar.pages import page_for, rejection
from allstar.parse_allstar import all_star_rosters
from allstar.parse_player import read_player
from allstar.parse_show import ShowPlayer, show_top100
from allstar.parse_team import all_star_rows, read_team
from allstar.rows import TeamPage, build_rows, columns, table_labels
from allstar.site import site_data
from allstar.validate import roster_gaps, validate

log = logging.getLogger("build")

OUTPUT = config.ROOT / "data" / "output"
CSV_PATH = OUTPUT / "all_stars_2024_2026.csv"
GAPS_PATH = OUTPUT / "all_star_gaps.csv"
SITE_PATH = config.ROOT / "website" / "data.js"
GAP_COLUMNS = ["season_id", "player_id", "name", "gap"]


def cached(manifest: dict[str, dict], url: str) -> bytes:
    """The cached page for url, only if it is exactly the page its manifest entry recorded."""
    entry = manifest.get(url)
    if entry is None:
        raise ValueError(f"{url} is not in the cache; run python scrape.py first")
    # The path comes from the URL, as scrape.py stores it, never from the entry.
    page = page_for(url)
    path = config.RAW_DIR / page.path
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
        raise ValueError(
            f"{shown(path)} differs from its manifest entry; "
            "python scrape.py --check-cache lists every such page"
        )
    # The page's own canonical link ties it to the URL, even if the manifest was edited too.
    problem = rejection(page, body)
    if problem:
        raise ValueError(f"{shown(path)} is not the page for {url}: {problem}")
    return body


def team_pages(manifest: dict[str, dict]) -> list[TeamPage]:
    """Every team page the league pages list, as scrape.py finds them."""
    pages = []
    for season in config.SEASONS:
        for url in team_urls(cached(manifest, league_url(season)), season):
            page = cached(manifest, url)
            code = url.rsplit("/", 2)[1]
            try:
                team, rows = read_team(page, code, season), all_star_rows(page, season)
            except ValueError as exc:
                raise ValueError(f"{url}: {exc}") from exc
            fetched_at = manifest[url].get("fetched_at")
            if not isinstance(fetched_at, str) or not fetched_at:
                raise ValueError(f"{url}: its manifest entry has no fetch time")
            pages.append(TeamPage(url, team, rows, fetched_at))
    return pages


def shown(path: Path) -> str:
    """A path as the reader typed it: relative to the project when it is inside it."""
    return str(path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path)


def csv_text(rows: list[dict[str, str]], fieldnames: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def write_outputs(files: dict[Path, bytes]) -> None:
    """Write every file to a synced temp file, then rename each into place.

    A failed temp write replaces nothing. The renames run back to back; if one fails, the
    files renamed before it are already new, and the error names them.
    """
    staged: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    try:
        for path, data in files.items():
            staged.append((stage(path, data), path))
        for tmp, path in staged:
            os.replace(tmp, path)
            replaced.append(path)
    except OSError as exc:
        if replaced:
            new = ", ".join(shown(path) for path in replaced)
            raise OSError(f"{exc}; already replaced: {new}") from exc
        raise
    finally:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)


def report(
    rows: list[dict[str, str]],
    matches: dict[str, ShowPlayer],
    rosters: dict[int, dict[str, str]],
    gaps: list[dict[str, str]],
) -> None:
    """Per season: the rows and where the extra ones come from, the roster, the Show rate."""
    for season in config.SEASONS:
        season_rows = [r for r in rows if r["season_id"] == str(season)]
        tables: dict[str, set[str]] = {}
        teams: dict[tuple[str, str], set[str]] = {}
        for r in season_rows:
            tables.setdefault(r["player_id"], set()).add(r["stat_type"])
            teams.setdefault((r["player_id"], r["stat_type"]), set()).add(r["team_id"])
        in_both = sum(len(kinds) - 1 for kinds in tables.values())
        traded = sum(len(clubs) - 1 for clubs in teams.values())
        on_show = sum(pid in matches for pid in tables)
        log.info(
            "%d: %d rows = %d All-Stars + %d rows from players in both tables + %d from trades; "
            "game roster %d; on the Show top 100: %d of %d (%d%%)",
            season,
            len(season_rows),
            len(tables),
            in_both,
            traded,
            len(rosters[season]),
            on_show,
            len(tables),
            round(100 * on_show / len(tables)) if tables else 0,
        )
    everyone = {r["player_id"] for r in rows}
    log.info("Show top-100 matches: %d of %d All-Stars", len(matches), len(everyone))
    log.info("roster gaps: %d", len(gaps))
    for gap in gaps:
        log.warning("  %s %s (%s): %s", gap["season_id"], gap["name"], gap["player_id"], gap["gap"])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        # A build killed while writing leaves its temp files. Run one build at a time: a
        # second build started mid-write removes the first one's, and the first then stops.
        for folder in sorted({CSV_PATH.parent, GAPS_PATH.parent, SITE_PATH.parent}):
            for leftover in remove_partial_writes(folder):
                log.warning("removed %s, left by an interrupted or running build", shown(leftover))
        manifest = load_manifest(config.RAW_DIR)
        pages = team_pages(manifest)
        labels = table_labels(pages)
        fieldnames = columns(pages)
        urls = {row.player_url: row.player_id for page in pages for row in page.rows}
        players = {
            pid: read_player(cached(manifest, url), pid) for url, pid in sorted(urls.items())
        }
        matches, mismatches = match_show(players, show_top100(cached(manifest, config.SHOW_URL)))
        rows = build_rows(pages, players, matches)
        rosters = {
            season: {
                spot.player_id: spot.name
                for spot in all_star_rosters(cached(manifest, allstar_url(season)), season)
            }
            for season in config.SEASONS
        }
    except (ValueError, OSError) as exc:
        log.error("stopped: %s", exc)
        return 1
    gaps = roster_gaps(rows, rosters)
    report(rows, matches, rosters, gaps)
    # A namesake with other hands would take another player's rating, so it fails the build.
    problems = validate(rows, fieldnames, labels)
    problems += [f"bats/throws differ: {mismatch}" for mismatch in mismatches]
    for problem in problems:
        log.error("check failed: %s", problem)
    if problems:
        log.error("nothing written: %d checks failed", len(problems))
        return 1
    try:
        write_outputs(
            {
                CSV_PATH: csv_text(rows, fieldnames),
                GAPS_PATH: csv_text(gaps, GAP_COLUMNS),
                SITE_PATH: site_data(rows),
            }
        )
    except OSError as exc:
        log.error("stopped: could not write the output: %s", exc)
        return 1
    log.info("wrote %s: %d rows, %d columns", shown(CSV_PATH), len(rows), len(fieldnames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
