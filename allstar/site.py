"""The website's data file: the CSV's rows as a script the page loads.

The site then needs no server-side code and no CSV parsing in the browser (birth places hold
commas), and it works from any static file server.
"""

import json

# The columns the page shows, sorts, filters or links, named as in the CSV.
SITE_COLUMNS = [
    *["player_id", "full_name", "season_id", "stat_type", "team_id", "team_name", "team_record"],
    *["all_star_selections_2024_2026", "positions_played", "primary_position", "bats", "throws"],
    *["is_show_top100", "show_overall_rating", "show_rank", "show_potential_grade"],
    *["HR", "OPS", "OPS+", "W", "ERA", "SO", "WHIP", "source_player_url"],
]


def site_data(rows: list[dict[str, str]]) -> bytes:
    """website/data.js: every row in the CSV's order, and the latest fetch as the as-of time."""
    payload = {
        "asOf": max(row["scraped_at"] for row in rows),
        "rows": [{column: row[column] for column in SITE_COLUMNS} for row in rows],
    }
    # ASCII only (json escapes the rest), so the file reads the same under any charset.
    text = json.dumps(payload, separators=(",", ":"))
    return f"// Written by build.py from the CSV's rows.\nwindow.ALL_STARS = {text};\n".encode()
