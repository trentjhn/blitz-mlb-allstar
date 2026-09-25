"""What a real page of each kind looks like, and where it lives in the cache."""

import re
from dataclasses import dataclass

# URL pattern, cache path, minimum size, and text only that page type carries. Path and
# marker placeholders are filled from the pattern's groups. These rules sit together here
# rather than in config.py because each row describes one page type.
PAGE_RULES = (
    (
        r"https://www\.theshowratings\.com/lists/top-100-players",
        "the_show/top-100-players.html",
        20_000,
        ("Top 100 Players on MLB The Show 26",),
    ),
    (
        r"https://www\.baseball-reference\.com/leagues/majors/(\d{4})\.shtml",
        "seasons/{0}.shtml",
        50_000,
        ("teams_standard_batting",),
    ),
    (
        r"https://www\.baseball-reference\.com/teams/([A-Z]{3})/(\d{4})\.shtml",
        "teams/{0}_{1}.shtml",
        50_000,
        ("players_standard_batting", "players_standard_pitching"),
    ),
    (
        r"https://www\.baseball-reference\.com/allstar/(\d{4})-allstar-game\.shtml",
        "allstar/{0}-allstar-game.shtml",
        50_000,
        ("NLAllStarsbatting", "ALAllStarsbatting"),
    ),
    (
        r"https://www\.baseball-reference\.com/players/([a-z])/(\1[a-z0-9]+)\.shtml",
        "players/{1}.shtml",
        50_000,
        ("Born:",),
    ),
)


@dataclass(frozen=True)
class Page:
    path: str
    min_bytes: int
    markers: tuple[bytes, ...]


def page_for(url: str) -> Page:
    """Resolve a URL to its cache path and page checks, or raise ValueError."""
    for pattern, path, min_bytes, markers in PAGE_RULES:
        match = re.fullmatch(pattern, url)
        if match:
            groups = match.groups()
            # Every real page names its own URL as canonical, which ties the body to the
            # request: a different team, season, or page type cannot pass.
            canonical = f'<link rel="canonical" href="{url}"'
            checks = (canonical, *(m.format(*groups) for m in markers))
            return Page(path.format(*groups), min_bytes, tuple(c.encode() for c in checks))
    raise ValueError(f"no cache rule for {url}")


def rejection(page: Page, body: bytes) -> str | None:
    """Why a response is not a complete, real page, or None if it passes."""
    if len(body) < page.min_bytes:
        return f"{len(body)} bytes, expected at least {page.min_bytes}"
    if not body.rstrip().endswith(b"</html>"):
        return "cut off: it does not end with </html>"
    missing = [m.decode() for m in page.markers if m not in body]
    return f"missing expected text {missing}" if missing else None
