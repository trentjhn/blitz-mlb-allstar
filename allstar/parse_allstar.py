"""Read Baseball Reference All-Star game pages: both leagues' full rosters."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from allstar.markup import find_by_id

PLAYER_HREF = re.compile(r"/players/[a-z]/([a-z0-9]+)\.shtml")
# The bold labels that split a roster table, mapped to the role of the rows after them. The
# starters come first, unlabelled; the manager's row links to /managers/, not to a player.
# Any other label stops the run rather than guess.
SECTIONS = {"manager": "manager", "reserves": "reserve"}


@dataclass(frozen=True)
class RosterSpot:
    season: int
    league: str
    player_id: str
    name: str
    position: str
    role: str


def all_star_rosters(page: bytes, season: int) -> list[RosterSpot]:
    """Every player named to either roster, from the page's lineups section.

    That section lists each league's starting lineup and starting pitcher, its manager, then
    every reserve, including players who were named but did not play. The box scores lower
    on the page list only the players who appeared, so they are not used.
    """
    soup = BeautifulSoup(page, "html.parser")
    lineups = find_by_id(soup, "div", "div_lineups")
    if lineups is None:
        raise ValueError(f"{season} All-Star page: no lineups section")
    spots: list[RosterSpot] = []
    for table in lineups.find_all("table"):
        words = table.caption.get_text(" ", strip=True).split() if table.caption else []
        league = words[0] if words else ""
        if league not in ("AL", "NL"):
            raise ValueError(f"{season} All-Star page: a roster for an unknown league {league!r}")
        role = "starter"
        for tr in table.find_all("tr"):
            link = tr.find("a", href=PLAYER_HREF)
            if link is None:
                label = tr.find("strong")
                if label is not None:
                    section = label.get_text(strip=True).lower()
                    if section not in SECTIONS:
                        raise ValueError(f"{season} All-Star page: an unknown section {section!r}")
                    role = SECTIONS[section]
                continue
            player_id = PLAYER_HREF.search(link["href"]).group(1)
            # A player under the manager means the page has changed: the Reserves label is
            # missing, say. Stopping beats counting the reserves as managers and dropping them.
            if role == "manager":
                raise ValueError(f"{season} All-Star page: {player_id} is listed under the manager")
            if any(spot.player_id == player_id for spot in spots):
                raise ValueError(f"{season} All-Star page: {player_id} is listed twice")
            cell = link.find_parent(["td", "th"])
            position = cell.find_next_sibling(["td", "th"]) if cell else None
            spots.append(
                RosterSpot(
                    season=season,
                    league=league,
                    player_id=player_id,
                    name=" ".join(link.get_text(" ").split()),
                    position=position.get_text(strip=True) if position else "",
                    role=role,
                )
            )
    return spots
