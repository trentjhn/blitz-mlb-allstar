"""Cached, polite fetching into data/raw/.

Use a Fetcher as a context manager: while open it holds the scrape lock (pacing.py). A cached
page is never requested again unless the caller forces a refresh, and then only once per run.
The first refusal or bad page stops the run. A refusal (403 or 429) also writes a block file,
and every later request, in any run, is refused until someone deletes it (pacing.py).
"""

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import requests

from allstar import config
from allstar.cache import (
    keep_rejected,
    load_manifest,
    manifest_entry,
    remove_partial_writes,
    save_manifest,
    write_atomic,
)
from allstar.download import Refused, download
from allstar.pacing import RequestPacer, StopScrape
from allstar.pages import Page, page_for, rejection

log = logging.getLogger(__name__)


def iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> str:
    return iso_utc(time.time())


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT
    return session


class Fetcher:
    def __init__(
        self,
        *,
        raw_dir: Path = config.RAW_DIR,
        quarantine_dir: Path = config.QUARANTINE_DIR,
        lock_path: Path = config.LOCK_PATH,
        block_path: Path = config.BLOCK_PATH,
        force: bool = False,
        offline: bool = False,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str] = utc_now,
    ) -> None:
        if force and offline:
            raise ValueError("force and offline cannot be combined")
        self.raw_dir = raw_dir
        self.quarantine_dir = quarantine_dir
        self.force = force
        self.offline = offline
        self.session = session or make_session()
        self.sleep = sleep
        self.now = now
        self.pacer = RequestPacer(lock_path, block_path=block_path, clock=clock, sleep=sleep)
        self.manifest: dict[str, dict] = {}
        self.stopped: str | None = None
        self._fetched_this_run: set[str] = set()

    @property
    def requests_made(self) -> int:
        return self.pacer.starts

    def __enter__(self) -> "Fetcher":
        self.pacer.__enter__()
        try:
            for leftover in remove_partial_writes(self.raw_dir):
                log.warning("removed %s, left by an interrupted write", leftover)
            self.manifest = load_manifest(self.raw_dir)
        except BaseException:
            self.pacer.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.pacer.__exit__(*exc_info)

    def get(self, url: str) -> bytes:
        if not self.pacer.is_open:
            raise RuntimeError("open the Fetcher with a with-statement first")
        if self.stopped:
            raise StopScrape(f"{url}: not handled, the run already stopped: {self.stopped}")
        page = page_for(url)
        path = self.raw_dir / page.path
        if not self.force or url in self._fetched_this_run:
            body = self._cached_body(url, page, path)
            if body is not None:
                return body
        if self.offline:
            raise StopScrape(f"{url} is not in the cache, and this run sends no requests")

        try:
            headers, body = download(self.session, self.pacer, self.sleep, url)
        except Refused as refused:
            self._stop(url, refused)
        problem = rejection(page, body)
        if problem:
            self._stop(url, Refused(f"rejected, {problem}", 200, headers, body))
        try:
            # A forced refresh drops the old entry first: an interrupted run then leaves an
            # unlisted page (kept next time) instead of new bytes under the old hash.
            if self.manifest.pop(url, None) is not None:
                save_manifest(self.raw_dir, self.manifest)
            write_atomic(path, body)
            self.manifest[url] = manifest_entry(page.path, body, self.now())
            save_manifest(self.raw_dir, self.manifest)
        except OSError as exc:
            # After a failed write nothing can trust the cache, so the run handles no more URLs.
            self.stopped = f"writing {page.path} failed: {exc}"
            raise StopScrape(f"{url}: {self.stopped}") from exc
        except BaseException as exc:
            self.stopped = f"writing {page.path} was cut short: {type(exc).__name__}"
            raise
        self._fetched_this_run.add(url)
        return body

    def _cached_body(self, url: str, page: Page, path: Path) -> bytes | None:
        """The cached page, or None when it has to be fetched."""
        if not path.is_file():
            return None
        body = path.read_bytes()
        entry = self.manifest.get(url)
        if entry is not None:
            if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
                raise StopScrape(
                    f"{path} differs from the manifest; delete that file to fetch it again"
                )
            return body
        # A run cut short between writing a page and its manifest entry leaves the page
        # unlisted. A complete, real page is kept, dated by its file time; anything else
        # goes to quarantine and is fetched again.
        if rejection(page, body) is None:
            self.manifest[url] = manifest_entry(page.path, body, iso_utc(path.stat().st_mtime))
            save_manifest(self.raw_dir, self.manifest)
            log.warning("kept %s, written by an interrupted run", path)
            return body
        keep_rejected(self.quarantine_dir, url, "unlisted, incomplete", None, {}, body, self.now())
        path.unlink()
        log.warning("moved %s to quarantine: an interrupted run left it incomplete", path)
        return None

    def _stop(self, url: str, refused: Refused) -> NoReturn:
        """Latch the stop first, then keep what the site sent, then raise."""
        self.stopped = refused.reason
        try:
            kept = keep_rejected(
                self.quarantine_dir,
                url,
                refused.reason,
                refused.status,
                refused.headers,
                refused.body,
                self.now(),
            )
            note = f"response kept at {kept}"
        except OSError as exc:
            note = f"the response could not be kept: {exc}"
        raise StopScrape(f"{url}: {refused.reason}; {note}")
