"""Which pages a run needs, read from pages already fetched."""

import re

from allstar import config
from allstar.pages import page_for


def league_url(season: int) -> str:
    return f"{config.BR_BASE}/leagues/majors/{season}.shtml"


def allstar_url(season: int) -> str:
    return f"{config.BR_BASE}/allstar/{season}-allstar-game.shtml"


def team_urls(league_page: bytes, season: int) -> list[str]:
    """The season's team pages, read from its league page's links, in team-code order.

    Reading them from the page instead of a hardcoded list picks up franchise moves
    (the Athletics were OAK in 2024) without anyone having to know about them.
    """
    html = league_page.decode("utf-8", errors="replace")
    codes = sorted(set(re.findall(rf'href="/teams/([A-Z]{{3}})/{season}\.shtml"', html)))
    if len(codes) != config.TEAMS_PER_SEASON:
        # The page stays cached so it can be inspected. Deleting it re-fetches that one page,
        # where --force would re-fetch every page.
        cached = config.RAW_DIR / page_for(league_url(season)).path
        raise ValueError(
            f"the {season} league page links {len(codes)} teams, expected "
            f"{config.TEAMS_PER_SEASON}. It is cached at {cached}; delete it to fetch a fresh copy"
        )
    return [f"{config.BR_BASE}/teams/{code}/{season}.shtml" for code in codes]
