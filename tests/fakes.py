"""Fake HTTP responses, session, and clock, so fetcher tests never touch the network or sleep."""

from allstar.fetch import Fetcher
from allstar.pages import page_for

TEAM_URL = "https://www.baseball-reference.com/teams/NYY/2024.shtml"
OTHER_TEAM_URL = "https://www.baseball-reference.com/teams/BOS/2024.shtml"


def team_page(url: str = TEAM_URL, filler: int = 60_000, extra: bytes = b"") -> bytes:
    """A minimal page that passes the team rule's checks for this URL."""
    head = f'<html><head><link rel="canonical" href="{url}" /></head>'.encode()
    tables = b"<body>players_standard_batting players_standard_pitching"
    return head + tables + extra + b" " * filler + b"</body></html>\n"


TEAM_PAGE = team_page()


def passing_page(url: str, extra: bytes = b"") -> bytes:
    """A page that passes the write gate for any cached URL: its checks, filler, a closing tag."""
    page = page_for(url)
    # The first check is the canonical link, left open by page_for; close it so the rest parses.
    canonical, *markers = page.markers
    head = b"<html><head>" + canonical + b" /></head><body>" + b" ".join(markers)
    return head + extra + b" " * page.min_bytes + b"</body></html>\n"


def team_page_with_all_stars(url: str, season: int, batters=(), pitchers=()) -> bytes:
    """A team page that passes the write gate and marks these player ids as All-Stars."""

    def table(table_id, columns, player_ids):
        head = "".join(f'<th data-stat="{c}">{c}</th>' for c in columns)
        cells = {
            "name_display": '<a href="/players/{letter}/{id}.shtml">{id}</a>',
            "awards": f'<a href="/allstar/{season}-allstar-game.shtml">AS</a>',
        }
        rows = "".join(
            "<tr>"
            + "".join(
                f'<td data-stat="{c}">' + cells.get(c, "1").format(letter=pid[0], id=pid) + "</td>"
                for c in columns
            )
            + "</tr>"
            for pid in player_ids
        )
        return f'<table id="{table_id}"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'

    batting = ["name_display", "age", "team_position", "b_hr", "pos", "awards"]
    pitching = ["name_display", "age", "team_position", "p_w", "awards"]
    tables = table("players_standard_batting", batting, batters) + table(
        "players_standard_pitching", pitching, pitchers
    )
    return passing_page(url, tables.encode())


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


class FakeSite:
    """Serves a fixed set of pages by URL, 404 for anything else, and records each request."""

    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url in self.pages:
            return FakeResponse(200, self.pages[url])
        return FakeResponse(404)


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
