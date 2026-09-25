"""Read the MLB The Show 26 top-100 list: each player's rank, name, team, and ratings."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from allstar import config


@dataclass(frozen=True)
class ShowPlayer:
    rank: int
    name: str
    team: str
    position: str
    bats: str
    throws: str
    overall: int
    potential: str


def show_top100(page: bytes) -> list[ShowPlayer]:
    """Every ranked row of the list, in rank order. The rows between them are ads."""
    tables = BeautifulSoup(page, "html.parser").find_all("table")
    if len(tables) != 1:
        raise ValueError(f"Show list: expected one table, found {len(tables)}")
    header = [th.get_text(strip=True) for th in tables[0].find_all("th")]
    if header != ["#", "Player", "OVR", "POT"]:
        raise ValueError(f"Show list: unexpected columns {header}")
    players = []
    for tr in tables[0].find_all("tr"):
        counter = tr.find("td", class_="counter")
        if counter is None:
            continue
        cells = tr.find_all("td", recursive=False)
        name = tr.select_one("span.entry-font a")
        details = tr.select_one("span.entry-subtext-font")
        team = details.find("a", href=re.compile(r"/teams/")) if details else None
        # "#99 Yankees | Outfielder | R/R". Position links vary (a DH's is empty), so the
        # position and hands come from the text.
        parts = (
            [part.strip() for part in details.get_text(" ", strip=True).split("|")]
            if details
            else []
        )
        hands = re.fullmatch(r"([RLS])/([RL])", parts[2]) if len(parts) == 3 else None
        ratings = [cell.get_text(strip=True) for cell in cells[2:]]
        rated = len(cells) == 4 and re.fullmatch(r"\d{1,2} [A-F]", " ".join(ratings))
        if not rated or None in (name, team, hands) or not name.get_text(strip=True):
            raise ValueError(f"Show list row {counter.get_text(strip=True)} has an unknown layout")
        position = parts[1]
        bats, throws = hands.groups()
        players.append(
            ShowPlayer(
                rank=int(counter.get_text(strip=True).rstrip(".")),
                name=" ".join(name.get_text(" ").split()),
                team=team.get_text(strip=True),
                position=position,
                bats=bats,
                throws=throws,
                overall=int(ratings[0]),
                potential=ratings[1],
            )
        )
    ranks = [player.rank for player in players]
    if ranks != list(range(1, config.SHOW_LIST_SIZE + 1)):
        raise ValueError(f"Show list: expected ranks 1-{config.SHOW_LIST_SIZE}, found {ranks}")
    return players
