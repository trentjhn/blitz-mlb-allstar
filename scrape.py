"""Fetch every page the dataset needs into data/raw/, politely and at most once.

Order: the MLB The Show top-100 list, then each season's league page (which lists that
season's team pages), then every team page, then each season's All-Star game page, then
the player page of every All-Star the team pages mark. Cached pages are read from disk and
never requested again.

    python scrape.py                 fetch only what is missing (nothing, on a full cache)
    python scrape.py --force         re-fetch every page, once each
    python scrape.py --offline       read the cache only; a missing page is an error
    python scrape.py --check-cache   verify data/raw/ is complete and matches its manifest

A run can be stopped at any moment (Ctrl-C, kill, a closed terminal). Every cache write is
atomic, the lock frees itself when the process ends, and the next run skips what is cached.
"""

import argparse
import logging
import signal
import sys
from collections.abc import Callable

from allstar import config
from allstar.cache import check_cache
from allstar.discover import allstar_url, league_url, team_urls
from allstar.fetch import Fetcher, StopScrape
from allstar.pages import page_for
from allstar.parse_team import all_star_rows

log = logging.getLogger("scrape")


def fetch_all(fetcher: Fetcher, report: Callable[..., None] = log.info) -> list[str]:
    """Fetch, or read from the cache, every page in order. Returns the URLs handled."""
    handled: list[str] = []

    def get(url: str) -> bytes:
        before = fetcher.requests_made
        body = fetcher.get(url)
        handled.append(url)
        report(
            "%4d %-7s %s",
            len(handled),
            "fetched" if fetcher.requests_made > before else "cached",
            url,
        )
        return body

    get(config.SHOW_URL)
    teams = {season: team_urls(get(league_url(season)), season) for season in config.SEASONS}
    players: set[str] = set()
    for season, urls in teams.items():
        for url in urls:
            page = get(url)
            try:
                rows = all_star_rows(page, season)
            except ValueError as exc:
                raise ValueError(f"{url}: {exc}") from exc
            players.update(row.player_url for row in rows)
    for season in config.SEASONS:
        get(allstar_url(season))
    # A player who was an All-Star in several seasons, or for two teams, is fetched once.
    # Every URL is checked against the cache rules first, so a bad link stops the run
    # before this stage sends anything.
    ordered = sorted(players)
    for url in ordered:
        page_for(url)
    for url in ordered:
        get(url)
    return handled


def _stop_on_signal(signum: int, frame: object) -> None:
    raise KeyboardInterrupt(signum)


def handle_signals() -> None:
    """Make a kill or a hangup stop the run the way Ctrl-C does, so the log says why.

    A signal the parent chose to ignore stays ignored: nohup ignores SIGHUP.
    """
    for sig in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(sig) is not signal.SIG_IGN:
            signal.signal(sig, _stop_on_signal)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="re-fetch every page, once each")
    mode.add_argument("--offline", action="store_true", help="read the cache only")
    mode.add_argument("--check-cache", action="store_true", help="verify data/raw/ and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    paths = {
        "raw_dir": config.RAW_DIR,
        "quarantine_dir": config.QUARANTINE_DIR,
        "lock_path": config.LOCK_PATH,
        "block_path": config.BLOCK_PATH,
    }
    try:
        if args.check_cache:
            problems = check_cache(config.RAW_DIR)
            for problem in problems:
                log.error(problem)
            if problems:
                return 1
            # Walking the whole run offline proves every page the dataset needs is present.
            with Fetcher(offline=True, **paths) as fetcher:
                handled = fetch_all(fetcher, report=lambda *args: None)
            log.info("cache complete: %d pages, all matching the manifest", len(handled))
            return 0
        with Fetcher(force=args.force, offline=args.offline, **paths) as fetcher:
            handled = fetch_all(fetcher)
        log.info("done: %d pages, %d requests", len(handled), fetcher.requests_made)
        return 0
    except (StopScrape, ValueError, OSError) as exc:
        # A refusal, an unexpected page, or a disk problem: say what happened and stop.
        log.error("stopped: %s", exc)
        return 1
    except KeyboardInterrupt as exc:
        # Ctrl-C, or a kill or hangup that handle_signals turned into one.
        signum = exc.args[0] if exc.args else signal.SIGINT
        log.error("stopped by %s; run again to resume", signal.Signals(signum).name)
        return 128 + signum


if __name__ == "__main__":
    handle_signals()
    sys.exit(main())
