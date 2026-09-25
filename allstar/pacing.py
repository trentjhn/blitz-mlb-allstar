"""One scrape at a time, and never two requests closer together than the configured spacing.

The lock file doubles as the record of when the last request started, so the spacing holds
across separate runs, not only within one.
"""

import fcntl
import os
import time
from collections.abc import Callable
from pathlib import Path

from allstar import config


class StopScrape(Exception):
    """The run has to stop: a refusal, a bad page, a URL that keeps failing, or a busy lock."""


class RequestPacer:
    """Holds the scrape lock while open, and makes each request wait for its turn."""

    def __init__(
        self,
        lock_path: Path = config.LOCK_PATH,
        *,
        block_path: Path = config.BLOCK_PATH,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.lock_path = lock_path
        self.block_path = block_path
        self.clock = clock
        self.sleep = sleep
        self.starts = 0
        self._fd: int | None = None
        self._last_start: float | None = None

    def __enter__(self) -> "RequestPacer":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # O_NOFOLLOW: a symlink planted at the lock path is refused, never written through.
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        except OSError as exc:
            raise StopScrape(f"cannot open the lock file {self.lock_path}: {exc}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise StopScrape(
                f"another scrape is running ({self.lock_path} is locked; the lock clears itself "
                "when that run ends)"
            ) from None
        self._fd = fd
        try:
            self._last_start = float(os.pread(fd, 64, 0))
        except ValueError:
            self._last_start = None
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def wait_turn(self) -> None:
        """Sleep until the spacing since the last request has passed, then record this start."""
        if self._fd is None:
            raise RuntimeError("the pacer is not open")
        # A deleted lock file would let a second scrape lock a new one and overlap with us.
        if os.fstat(self._fd).st_nlink == 0:
            raise StopScrape(f"{self.lock_path} was deleted during the run; stopping")
        # Every request passes through here, so a refusal recorded by any earlier run stops
        # this one before it sends, while work that needs only the cache still runs.
        if self.block_path.exists():
            reason = self.block_path.read_text(errors="replace").strip()
            raise StopScrape(
                f"the site refused an earlier request ({reason}). "
                f"Delete {self.block_path} once the block has lifted; they can last a day."
            )
        if self._last_start is not None:
            # Capped, so a clock that jumps backwards cannot turn into a long stall.
            wait = min(
                config.REQUEST_SPACING_S,
                self._last_start + config.REQUEST_SPACING_S - self.clock(),
            )
            if wait > 0:
                self.sleep(wait)
        self._last_start = self.clock()
        os.ftruncate(self._fd, 0)
        os.pwrite(self._fd, f"{self._last_start:.3f}\n".encode(), 0)
        self.starts += 1
