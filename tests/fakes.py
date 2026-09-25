"""Fake HTTP responses, session, and clock, so fetcher tests never touch the network or sleep."""

from allstar.fetch import Fetcher

TEAM_URL = "https://www.baseball-reference.com/teams/NYY/2024.shtml"
OTHER_TEAM_URL = "https://www.baseball-reference.com/teams/BOS/2024.shtml"


def team_page(url: str = TEAM_URL, filler: int = 60_000, extra: bytes = b"") -> bytes:
    """A minimal page that passes the team rule's checks for this URL."""
    head = f'<html><head><link rel="canonical" href="{url}" /></head>'.encode()
    tables = b"<body>players_standard_batting players_standard_pitching"
    return head + tables + extra + b" " * filler + b"</body></html>\n"


TEAM_PAGE = team_page()


class FakeResponse:
    def __init__(self, status_code, content=b"", headers=None, read_error=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self._read_error = read_error

    @property
    def content(self):
        if self._read_error:
            raise self._read_error
        return self._content


class FakeClock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeSession:
    """Plays back queued responses (or raises queued exceptions) and records each call."""

    def __init__(self, clock, *replies):
        self.clock = clock
        self.replies = list(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, "at": self.clock.now, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_fetcher(tmp_path, clock, *replies, **options):
    session = FakeSession(clock, *replies)
    options.setdefault("now", lambda: "2026-09-25T04:00:00Z")
    options.setdefault("block_path", tmp_path / ".scrape.blocked")
    fetcher = Fetcher(
        raw_dir=tmp_path / "raw",
        quarantine_dir=tmp_path / "quarantine",
        lock_path=tmp_path / ".scrape.lock",
        session=session,
        clock=clock,
        sleep=clock.sleep,
        **options,
    )
    return fetcher, session
