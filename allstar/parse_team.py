"""Read Baseball Reference team pages: the team's record, and every All-Star's stat line."""

import html
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from allstar import config
from allstar.markup import find_by_id

# Regular-season tables only. The same page has *_post tables for the postseason, which the
# spec rules out, so tables are found by exact id and never by prefix.
STAT_TABLES = {"batting": "players_standard_batting", "pitching": "players_standard_pitching"}
PLAYER_HREF = re.compile(r"/players/[a-z]/([a-z0-9]+)\.shtml")
# Cells that describe the player rather than count something. Everything else is a stat,
# named by the table's own header (HR, OPS+, ERA, ...).
NOT_STATS = {"ranker", "name_display", "age", "team_position", "pos", "awards"}
# Columns each row is read from besides its stats. A missing column, in the header or in an
# All-Star's row, stops the run instead of leaving a field empty. The pitching table has no
# positions-played column.
NEEDED = {
    "batting": {"name_display", "team_position", "pos", "awards"},
    "pitching": {"name_display", "team_position", "awards"},
}


@dataclass(frozen=True)
class Team:
    team_id: str
    season: int
    name: str
    wins: int
    losses: int


@dataclass(frozen=True)
class AllStarRow:
    player_id: str
    player_url: str
    name: str
    stat_type: str
    primary_position: str
    positions_played: str
    stats: dict[str, str]


def read_team(page: bytes, team_id: str, season: int) -> Team:
    # Both facts sit in the page header, so a text search is enough; no need to parse the page.
    # Comments are dropped first, so a stale heading or record kept in one is never read.
    text = re.sub(r"<!--.*?-->", "", page.decode("utf-8", "replace"), flags=re.DOTALL)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.DOTALL)
    heading = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h1.group(1))).split()) if h1 else ""
    name = re.fullmatch(rf"{season} (.+?) Statistics", heading)
    record = re.search(r"<strong>Record:</strong>\s*(\d+)-(\d+)", text)
    if name is None or record is None:
        raise ValueError(f"{team_id} {season}: no team name or record in the page header")
    return Team(team_id, season, name.group(1), int(record.group(1)), int(record.group(2)))


def game_link(season: int) -> re.Pattern:
    """This season's All-Star game link on this site, relative or absolute, with any query."""
    site = r"(?:(?:https?:)?//www\.baseball-reference\.com)?"
    return re.compile(rf"^{site}/allstar/{season}-allstar-game\.shtml(?:[?#]|$)")


def stat_labels(table: Tag, table_id: str, needed: set[str]) -> dict[str, str]:
    """Each stat column's data-stat, mapped to the label the table shows for it."""
    # The last header row names the columns; rows above it (group headings) do not.
    header = table.thead.find_all("tr")
    columns = [th for th in header[-1].find_all("th") if th.get("data-stat")] if header else []
    missing = needed - {th["data-stat"] for th in columns}
    if missing:
        raise ValueError(f"{table_id}: no {', '.join(sorted(missing))} column in the header")
    labels = {
        th["data-stat"]: th.get_text(strip=True)
        for th in columns
        if th["data-stat"] not in NOT_STATS
    }
    # Two columns under one label would silently overwrite each other in a row's stats.
    if len(set(labels.values())) != len(labels):
        raise ValueError(f"{table_id}: two stat columns share a label: {labels}")
    return labels


def all_star_rows(page: bytes, season: int) -> list[AllStarRow]:
    """Every row whose Awards cell links to this season's All-Star game, from both tables."""
    soup = BeautifulSoup(page, "html.parser")
    game = game_link(season)
    rows = []
    for stat_type, table_id in STAT_TABLES.items():
        table = find_by_id(soup, "table", table_id)
        if table is None or table.thead is None or table.tbody is None:
            raise ValueError(f"{season} team page: no {table_id} table with a header and a body")
        labels = stat_labels(table, table_id, NEEDED[stat_type])
        for tr in table.select("tbody > tr"):
            awards = tr.find(attrs={"data-stat": "awards"})
            # Every row has an Awards cell, the header rows repeated in the body included, so a
            # row without one means the table changed.
            if awards is None:
                raise ValueError(f"{season} {table_id}: a row has no awards cell")
            if awards.find("a", href=game) is None:
                continue
            cells = {td["data-stat"]: td for td in tr.find_all(["td", "th"]) if td.get("data-stat")}
            missing = (NEEDED[stat_type] | labels.keys()) - cells.keys()
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(f"{season} {table_id}: an All-Star row has no {names} cell")
            # The link text is the bare name; handedness marks such as * and # sit outside it.
            link = cells["name_display"].find("a", href=PLAYER_HREF)
            if link is None:
                raise ValueError(f"{season} {table_id}: an All-Star row has no player link")
            href = PLAYER_HREF.search(link["href"])
            text = {stat: cell.get_text(strip=True) for stat, cell in cells.items()}
            rows.append(
                AllStarRow(
                    player_id=href.group(1),
                    player_url=f"{config.BR_BASE}{href.group(0)}",
                    name=" ".join(link.get_text(" ").split()),
                    stat_type=stat_type,
                    primary_position=text["team_position"],
                    positions_played=text.get("pos", ""),
                    stats={label: text[stat] for stat, label in labels.items()},
                )
            )
    return rows
