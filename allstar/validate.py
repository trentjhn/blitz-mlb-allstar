"""The checks the built rows must pass before anything is written."""

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from allstar import config

# Every column the spec names, which the CSV may extend but not drop.
REQUIRED = [
    *["player_id", "team_id", "season_id", "stat_type"],
    *["is_all_star", "all_star_selections_2024_2026"],
    *["full_name", "birth_date", "birth_place", "bats", "throws", "height_inches", "weight_lbs"],
    *["debut_date", "primary_position"],
    *["team_name", "team_wins", "team_losses", "team_record"],
    *["G", "PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "CS", "BB", "SO"],
    *["BA", "OBP", "SLG", "OPS", "OPS+"],
    *["W", "L", "W-L%", "ERA", "GS", "GF", "CG", "SHO", "SV", "IP", "ER", "WHIP", "ERA+"],
    *["scraped_at", "source_team_url", "source_player_url"],
    *["is_show_top100", "show_overall_rating", "show_rank", "show_potential_grade"],
]
ALLOWED = {
    "stat_type": {"batting", "pitching"},
    "season_id": {str(season) for season in config.SEASONS},
    "bats": {"R", "L", "S"},
    "throws": {"R", "L"},
    "is_all_star": {"true"},
    "is_show_top100": {"true", "false"},
}
FORMATS = {
    "birth_date": r"\d{4}-\d{2}-\d{2}",
    "debut_date": r"\d{4}-\d{2}-\d{2}",
    "scraped_at": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
    "height_inches": r"\d{2}",
    "weight_lbs": r"\d{3}",
    "team_wins": r"\d{1,3}",
    "team_losses": r"\d{1,3}",
}
# The spec's counting stats, never blank on a row from their own table. Rates may be blank:
# the page leaves a rate empty when its denominator is 0, such as OPS at 0 PA.
COUNTS = {
    "batting": {"G", "PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "CS", "BB", "SO"},
    "pitching": {
        *["W", "L", "G", "GS", "GF", "CG", "SHO", "SV"],
        *["IP", "H", "R", "ER", "HR", "BB", "SO"],
    },
}
NUMBER = r"-?(\d+|\d*\.\d+)"
# Rows checked against values read by hand from the pages: a traded player's two splits, a
# closer, a foreign-born player, and the spec's own example with his profile dates. Only
# values that no longer change: 2024 stats, names and dates.
SPOT_CHECKS = {
    ("arraelu01", "2024", "batting", "MIA"): {"G": "33", "PA": "148", "HR": "0", "OPS": ".719"},
    ("arraelu01", "2024", "batting", "SDP"): {"G": "117", "PA": "524", "HR": "4", "OPS": ".744"},
    ("yateski01", "2024", "pitching", "TEX"): {"SV": "33", "ERA": "1.17", "SO": "85"},
    ("judgeaa01", "2024", "batting", "NYY"): {
        "HR": "58",
        "OPS": "1.159",
        "OPS+": "225",
        "team_name": "New York Yankees",
        "team_record": "94-68",
        "birth_date": "1992-04-26",
        "debut_date": "2016-08-13",
    },
    ("sotoju01", "2024", "batting", "NYY"): {
        "HR": "41",
        "OPS": ".989",
        "birth_place": "Santo Domingo, Dominican Republic",
    },
}


def key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (row["player_id"], row["season_id"], row["stat_type"], row["team_id"])


def validate(
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
    table_labels: Mapping[str, Sequence[str]],
) -> list[str]:
    """One line per problem; an empty list means the rows may be written.

    table_labels maps each stat type to its table's stat columns. A row may hold a value only
    in its own table's columns, so a batting line can never show a pitching stat.
    """
    stat_columns = {label for labels in table_labels.values() for label in labels}
    problems = [f"missing column {column}" for column in REQUIRED if column not in columns]
    problems += [f"{k}: appears {n} times" for k, n in Counter(map(key, rows)).items() if n > 1]
    seasons: dict[str, set[str]] = {}
    for row in rows:
        seasons.setdefault(row["player_id"], set()).add(row["season_id"])
    for row in rows:
        where = " ".join(key(row))
        for field in ("player_id", "team_id", "season_id", "stat_type", "full_name"):
            if not row[field]:
                problems.append(f"{where}: empty {field}")
        for field, allowed in ALLOWED.items():
            if row[field] not in allowed:
                problems.append(f"{where}: {field} is {row[field]!r}")
        for field, pattern in FORMATS.items():
            if not re.fullmatch(pattern, row[field]):
                problems.append(f"{where}: {field} is {row[field]!r}")
        if row["team_record"] != f"{row['team_wins']}-{row['team_losses']}":
            problems.append(f"{where}: team_record {row['team_record']} disagrees with W-L")
        if row["all_star_selections_2024_2026"] != str(len(seasons[row["player_id"]])):
            problems.append(f"{where}: selections {row['all_star_selections_2024_2026']}")
        show = [
            row[field] for field in ("show_overall_rating", "show_rank", "show_potential_grade")
        ]
        if (row["is_show_top100"] == "true") != all(show) or any(show) != all(show):
            problems.append(f"{where}: Show fields {show} disagree with is_show_top100")
        if not row["primary_position"]:
            problems.append(f"{where}: empty primary_position")
        if row["primary_position"] == "P" and "Pitcher" not in row["profile_position"]:
            problems.append(
                f"{where}: primary_position P, but the profile says {row['profile_position']!r}"
            )
        own = set(table_labels.get(row["stat_type"], ()))
        counts = COUNTS.get(row["stat_type"], set())
        for column in sorted(own):
            value = row[column]
            if (value or column in counts) and not re.fullmatch(NUMBER, value):
                problems.append(f"{where}: {column} is {value!r}")
        stray = sorted(column for column in stat_columns - own if row[column])
        if stray:
            problems.append(f"{where}: values in another table's columns {stray}")
    # Each season's team codes and names pair one to one, so a page read under another
    # team's code shows up as a name with two codes.
    pairs = {(r["season_id"], r["team_id"], r["team_name"]) for r in rows}
    per_code = Counter((season, code) for season, code, _ in pairs)
    per_name = Counter((season, name) for season, _, name in pairs)
    problems += [
        f"{s} {c}: more than one team_name" for (s, c), n in sorted(per_code.items()) if n > 1
    ]
    problems += [
        f"{s} {t}: more than one team_id" for (s, t), n in sorted(per_name.items()) if n > 1
    ]
    teams = Counter(season for season, _ in {(r["season_id"], r["team_id"]) for r in rows})
    for season in config.SEASONS:
        if teams[str(season)] != config.TEAMS_PER_SEASON:
            problems.append(f"{season}: All-Stars on {teams[str(season)]} teams, not all 30")
    by_key = {key(row): row for row in rows}
    for spot, expected in SPOT_CHECKS.items():
        row = by_key.get(spot)
        actual = {field: row.get(field) for field in expected} if row else None
        if actual != expected:
            problems.append(f"spot check {spot}: expected {expected}, found {actual}")
    return problems


def roster_gaps(
    rows: Sequence[Mapping[str, str]], rosters: Mapping[int, Mapping[str, str]]
) -> list[dict[str, str]]:
    """Players on a season's All-Star roster with no team-page mark, and the reverse."""
    gaps = []
    for season, roster in rosters.items():
        marked = {r["player_id"]: r["full_name"] for r in rows if r["season_id"] == str(season)}
        for player_id in sorted(roster.keys() - marked.keys()):
            gaps.append(
                {
                    "season_id": str(season),
                    "player_id": player_id,
                    "name": roster[player_id],
                    "gap": "on the game roster, no All-Star mark on a team page",
                }
            )
        for player_id in sorted(marked.keys() - roster.keys()):
            gaps.append(
                {
                    "season_id": str(season),
                    "player_id": player_id,
                    "name": marked[player_id],
                    "gap": "marked on a team page, not on the game roster",
                }
            )
    return gaps
