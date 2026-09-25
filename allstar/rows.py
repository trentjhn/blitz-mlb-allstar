"""Assemble the dataset: one row per All-Star, season, stat table, and team."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from allstar.parse_player import Player
from allstar.parse_show import ShowPlayer
from allstar.parse_team import AllStarRow, Team

# Column groups in the spec's order. Stat columns sit between TEAM and PROVENANCE; they are
# the tables' own labels, batting first, then the pitching labels batting does not share.
KEYS = ["player_id", "team_id", "season_id", "stat_type"]
SUMMARY = ["is_all_star", "all_star_selections_2024_2026"]
PLAYER = [
    "full_name",
    "birth_date",
    "birth_place",
    "bats",
    "throws",
    "height_inches",
    "weight_lbs",
    "debut_date",
    "primary_position",
    "profile_position",
    "positions_played",
]
TEAM = ["team_name", "team_wins", "team_losses", "team_record"]
PROVENANCE = ["scraped_at", "source_team_url", "source_player_url"]
SHOW = ["is_show_top100", "show_overall_rating", "show_rank", "show_potential_grade"]


@dataclass(frozen=True)
class TeamPage:
    url: str
    team: Team
    rows: list[AllStarRow]
    fetched_at: str


def table_labels(pages: Sequence[TeamPage]) -> dict[str, list[str]]:
    """Each table's stat labels, in page order, which every page with All-Star rows in that
    table must share. The other tables add no rows, so they are not read.

    A re-fetched page whose table gained, lost or renamed a column would otherwise add a
    column to the CSV, or leave blanks in one, and still pass every check.
    """
    seen: dict[str, dict[tuple[str, ...], set[str]]] = {"batting": {}, "pitching": {}}
    for page in pages:
        for row in page.rows:
            seen[row.stat_type].setdefault(tuple(row.stats), set()).add(page.url)
    labels = {}
    for stat_type, kinds in seen.items():
        usual = max(kinds, key=lambda kind: len(kinds[kind]), default=())
        for kind, urls in kinds.items():
            if kind != usual:
                missing = [label for label in usual if label not in kind]
                extra = [label for label in kind if label not in usual]
                change = f"missing {missing}, extra {extra}" if missing or extra else "reordered"
                raise ValueError(
                    f"{min(urls)}: its {stat_type} table's columns differ from the other "
                    f"pages' ({change})"
                )
        labels[stat_type] = list(usual)
    return labels


def stat_columns(pages: Sequence[TeamPage]) -> list[str]:
    labels = table_labels(pages)
    return list(dict.fromkeys(labels["batting"] + labels["pitching"]))


def primary_position(row: AllStarRow, page: TeamPage) -> str:
    """The team page's Pos for this row, else the Pos on the player's row in the other table.

    The cell is empty for a position player's pitching line and for some pitchers (5 rows in
    2024-2026). Only All-Star rows are searched, which covers all 5. A pitching row with no
    position anywhere is a pitcher, "P"; validate() fails a "P" whose profile is not a pitcher.
    """
    if row.primary_position:
        return row.primary_position
    for other in page.rows:
        if other.player_id == row.player_id and other.primary_position:
            return other.primary_position
    return "P" if row.stat_type == "pitching" else ""


def build_rows(
    pages: Sequence[TeamPage],
    players: Mapping[str, Player],
    show: Mapping[str, ShowPlayer],
) -> list[dict[str, str]]:
    """Every row, as text in the CSV's column order, sorted by season, team, table, player."""
    seasons: dict[str, set[int]] = {}
    for page in pages:
        for row in page.rows:
            seasons.setdefault(row.player_id, set()).add(page.team.season)
    stats = stat_columns(pages)
    out = []
    for page in pages:
        team = page.team
        for row in page.rows:
            player = players[row.player_id]
            entry = show.get(row.player_id)
            values = {
                "player_id": row.player_id,
                "team_id": team.team_id,
                "season_id": str(team.season),
                "stat_type": row.stat_type,
                "is_all_star": "true",
                "all_star_selections_2024_2026": str(len(seasons[row.player_id])),
                "full_name": player.full_name,
                "birth_date": player.birth_date,
                "birth_place": player.birth_place,
                "bats": player.bats,
                "throws": player.throws,
                "height_inches": str(player.height_inches),
                "weight_lbs": str(player.weight_lbs),
                "debut_date": player.debut_date,
                "primary_position": primary_position(row, page),
                "profile_position": player.position,
                "positions_played": row.positions_played,
                "team_name": team.name,
                "team_wins": str(team.wins),
                "team_losses": str(team.losses),
                "team_record": f"{team.wins}-{team.losses}",
                **{column: row.stats.get(column, "") for column in stats},
                "scraped_at": page.fetched_at,
                "source_team_url": page.url,
                "source_player_url": row.player_url,
                "is_show_top100": "true" if entry else "false",
                "show_overall_rating": str(entry.overall) if entry else "",
                "show_rank": str(entry.rank) if entry else "",
                "show_potential_grade": entry.potential if entry else "",
            }
            out.append(values)
    out.sort(key=lambda r: (r["season_id"], r["team_id"], r["stat_type"], r["player_id"]))
    return out


def columns(pages: Sequence[TeamPage]) -> list[str]:
    return KEYS + SUMMARY + PLAYER + TEAM + stat_columns(pages) + PROVENANCE + SHOW
