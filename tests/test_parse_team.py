import pytest

from allstar import config
from allstar.parse_team import all_star_rows, read_team

NYY_2024 = (config.RAW_DIR / "teams/NYY_2024.shtml").read_bytes()

# Each table's stat columns in page order, space-separated. They become the CSV's stat columns.
BATTING = (
    "WAR G PA AB R H 2B 3B HR RBI SB CS BB SO BA OBP SLG OPS OPS+ rOBA Rbat+ TB GIDP HBP SH SF IBB"
)
PITCHING = (
    "WAR W L W-L% ERA G GS GF CG SHO SV IP H R ER HR BB IBB SO HBP BK WP BF ERA+ FIP WHIP "
    "H9 HR9 BB9 SO9 SO/BB"
)
# The spec's minimum columns for each table.
SPEC_BATTING = "G PA AB R H 2B 3B HR RBI SB CS BB SO BA OBP SLG OPS OPS+"
SPEC_PITCHING = "W L W-L% ERA G GS GF CG SHO SV IP H R ER HR BB SO WHIP ERA+"


def by_player(rows):
    return {(row.player_id, row.stat_type): row for row in rows}


def edit_batting_table(old, new, table_id="players_standard_batting"):
    """NYY 2024 with one change inside one of its regular-season stat tables."""
    start = NYY_2024.rindex(b"<table", 0, NYY_2024.index(f'id="{table_id}"'.encode()))
    end = NYY_2024.index(b"</table>", start)
    table = NYY_2024[start:end]
    assert old in table
    return NYY_2024[:start] + table.replace(old, new, 1) + NYY_2024[end:]


def test_team_name_and_record_come_from_the_page_header():
    team = read_team(NYY_2024, "NYY", 2024)
    assert (team.name, team.wins, team.losses) == ("New York Yankees", 94, 68)


def test_all_star_rows_are_exactly_the_marked_players():
    rows = all_star_rows(NYY_2024, 2024)
    # Counted before any grouping, so a row read twice would show.
    assert len(rows) == 3
    assert sorted(by_player(rows)) == [
        ("holmecl01", "pitching"),
        ("judgeaa01", "batting"),
        ("sotoju01", "batting"),
    ]


def test_batting_row_carries_the_regular_season_line():
    judge = by_player(all_star_rows(NYY_2024, 2024))[("judgeaa01", "batting")]
    assert (judge.name, judge.primary_position, judge.positions_played) == (
        "Aaron Judge",
        "CF",
        "*8D/97",
    )
    assert judge.player_url == "https://www.baseball-reference.com/players/j/judgeaa01.shtml"
    # 58 home runs is his 2024 regular season; the postseason table on the same page has 3.
    assert {k: judge.stats[k] for k in ("G", "PA", "HR", "OPS", "OPS+")} == {
        "G": "158",
        "PA": "704",
        "HR": "58",
        "OPS": "1.159",
        "OPS+": "225",
    }


def test_pitching_row_uses_the_pitching_table_labels():
    holmes = by_player(all_star_rows(NYY_2024, 2024))[("holmecl01", "pitching")]
    assert holmes.primary_position == "CL"
    assert {k: holmes.stats[k] for k in ("W", "ERA", "SO", "WHIP")} == {
        "W": "3",
        "ERA": "3.14",
        "SO": "68",
        "WHIP": "1.302",
    }
    assert "OPS" not in holmes.stats


def test_a_table_hidden_in_a_comment_is_found():
    batting_start = NYY_2024.rindex(b"<table", 0, NYY_2024.index(b'id="players_standard_batting"'))
    batting_end = NYY_2024.index(b"</table>", batting_start) + len(b"</table>")
    hidden = (
        NYY_2024[:batting_start]
        + b"<!--"
        + NYY_2024[batting_start:batting_end]
        + b"-->"
        + NYY_2024[batting_end:]
    )
    rows = by_player(all_star_rows(hidden, 2024))
    assert ("judgeaa01", "batting") in rows


def test_another_seasons_all_star_link_does_not_count():
    assert all_star_rows(NYY_2024, 2025) == []


def test_two_stat_columns_with_one_label_are_an_error():
    page = NYY_2024.replace(b'data-tip="Home Runs" >HR<', b'data-tip="Home Runs" >RBI<', 1)
    assert page != NYY_2024
    with pytest.raises(ValueError, match="share a label"):
        all_star_rows(page, 2024)


def test_a_page_without_the_stat_tables_is_an_error():
    with pytest.raises(ValueError, match="players_standard_batting"):
        all_star_rows(b"<html><body>nothing here</body></html>", 2024)


def test_every_cached_team_page_has_clean_unique_all_star_rows(cached_teams):
    # Every club sends at least one player to the game, so a team with none means a miss.
    assert len(cached_teams) == 90
    for url, _, rows in cached_teams:
        assert rows, url
        keys = [(row.player_id, row.stat_type) for row in rows]
        assert len(keys) == len(set(keys)), url
        assert all(row.name and not set(row.name) & set("*#()") for row in rows), url


def test_every_team_page_gives_the_same_stat_columns_including_the_specs(cached_teams):
    assert set(SPEC_BATTING.split()).issubset(BATTING.split())
    assert set(SPEC_PITCHING.split()).issubset(PITCHING.split())
    for url, _, rows in cached_teams:
        for row in rows:
            expected = BATTING if row.stat_type == "batting" else PITCHING
            assert " ".join(row.stats) == expected, url


def test_every_cached_team_page_has_a_name_and_a_full_record(cached_teams):
    for url, team, _ in cached_teams:
        games = team.wins + team.losses
        # A full season is 162 games; a rainout never made up leaves 161. 2026 is in progress.
        full = 161 <= games <= 162 if team.season < 2026 else 0 < games <= 162
        assert team.name and full, url


def test_an_all_star_row_without_a_player_link_is_an_error():
    link = b'<a href="/players/j/judgeaa01.shtml">Aaron Judge</a>'
    at = NYY_2024.index(link, NYY_2024.index(b'id="players_standard_batting"'))
    page = NYY_2024[:at] + b"Aaron Judge" + NYY_2024[at + len(link) :]
    with pytest.raises(ValueError, match="an All-Star row has no player link"):
        all_star_rows(page, 2024)


def test_a_player_link_written_as_a_full_url_gives_the_same_row():
    page = NYY_2024.replace(
        b'href="/players/j/judgeaa01.shtml"',
        b'href="https://www.baseball-reference.com/players/j/judgeaa01.shtml?from=team"',
    )
    judge = by_player(all_star_rows(page, 2024))[("judgeaa01", "batting")]
    assert judge.player_url == "https://www.baseball-reference.com/players/j/judgeaa01.shtml"


@pytest.mark.parametrize(
    "href",
    [
        b"https://www.baseball-reference.com/allstar/2024-allstar-game.shtml",
        b"/allstar/2024-allstar-game.shtml?from=team",
        b"/allstar/2024-allstar-game.shtml#all_lineups",
    ],
)
def test_an_all_star_link_written_another_way_still_counts(href):
    page = NYY_2024.replace(b'href="/allstar/2024-allstar-game.shtml"', b'href="' + href + b'"')
    assert len(all_star_rows(page, 2024)) == 3


def test_a_link_to_a_different_page_does_not_count():
    page = NYY_2024.replace(
        b'href="/allstar/2024-allstar-game.shtml"', b'href="/allstar/2024-allstar-game.shtml.old"'
    )
    assert all_star_rows(page, 2024) == []


RENAMED = [
    *[("players_standard_batting", c) for c in ["name_display", "age", "team_position", "pos"]],
    ("players_standard_batting", "awards"),
    *[("players_standard_pitching", c) for c in ["name_display", "age", "team_position", "awards"]],
]


@pytest.mark.parametrize(("table_id", "column"), RENAMED)
def test_a_renamed_column_is_an_error_not_an_empty_field(table_id, column):
    header = f'data-stat="{column}" scope="col"'.encode()
    page = edit_batting_table(header, b'data-stat="x" scope="col"', table_id)
    with pytest.raises(ValueError, match=f"no {column} column"):
        all_star_rows(page, 2024)


def test_a_stat_column_renamed_in_the_header_is_an_error():
    # The rows keep a b_hr cell that no header column names, and lose the one the header does.
    page = edit_batting_table(b'data-stat="b_hr" scope="col"', b'data-stat="b_hrx" scope="col"')
    with pytest.raises(ValueError, match="an All-Star row has no b_hrx cell"):
        all_star_rows(page, 2024)


def test_an_all_star_row_missing_a_cell_is_an_error():
    name_cell = b'<a href="/players/j/judgeaa01.shtml">Aaron Judge</a></td>'
    age_cell = b' <td class="right " data-stat="age" >32</td>'
    page = edit_batting_table(name_cell + age_cell, name_cell)
    with pytest.raises(ValueError, match="an All-Star row has no age cell"):
        all_star_rows(page, 2024)


def test_the_heading_must_name_the_season():
    with pytest.raises(ValueError, match="no team name or record"):
        read_team(NYY_2024, "NYY", 2025)


@pytest.mark.parametrize("part", ["thead", "tbody"])
def test_a_stat_table_without_a_header_or_a_body_is_an_error(part):
    start = NYY_2024.rindex(b"<table", 0, NYY_2024.index(b'id="players_standard_batting"'))
    open_at = NYY_2024.index(f"<{part}>".encode(), start)
    close_at = NYY_2024.index(f"</{part}>".encode(), open_at) + len(part) + 3
    page = NYY_2024[:open_at] + NYY_2024[close_at:]
    with pytest.raises(ValueError, match="with a header and a body"):
        all_star_rows(page, 2024)


def test_a_row_of_group_headings_in_the_header_is_ignored():
    headings = (
        b'<thead><tr><th colspan="10">Batting</th><th colspan="5" data-stat="">Rates</th></tr>'
    )
    judge = by_player(all_star_rows(edit_batting_table(b"<thead>", headings), 2024))
    assert " ".join(judge[("judgeaa01", "batting")].stats) == BATTING


def test_rows_in_a_second_table_body_are_read():
    row = b'<tr > <th scope="row" class="right " data-stat="ranker" csk="2" >7</th>'
    page = edit_batting_table(row, b"</tbody><tbody>" + row)
    assert ("judgeaa01", "batting") in by_player(all_star_rows(page, 2024))


def test_a_name_with_non_breaking_spaces_reads_as_plain_words():
    page = edit_batting_table(b">Aaron Judge</a>", b">Aaron&nbsp;Judge</a>")
    assert by_player(all_star_rows(page, 2024))[("judgeaa01", "batting")].name == "Aaron Judge"


JUDGE_HR = b'<td class="right " data-stat="b_hr" ><strong><em>58</em></strong></td>'


@pytest.mark.parametrize(
    "cell",
    [b"", b'<td class="right " data-stat="b_hrx" ><strong><em>58</em></strong></td>'],
    ids=["deleted", "renamed"],
)
def test_an_all_star_row_missing_a_stat_cell_is_an_error(cell):
    with pytest.raises(ValueError, match="an All-Star row has no b_hr cell"):
        all_star_rows(edit_batting_table(JUDGE_HR, cell), 2024)


def test_a_page_without_the_record_line_is_an_error():
    page = NYY_2024.replace(b"<strong>Record:</strong>", b"<strong>Rec:</strong>")
    with pytest.raises(ValueError, match="no team name or record"):
        read_team(page, "NYY", 2024)


def test_an_all_star_link_to_another_site_does_not_count():
    page = NYY_2024.replace(
        b'href="/allstar/2024-allstar-game.shtml"',
        b'href="https://example.com/allstar/2024-allstar-game.shtml"',
    )
    assert all_star_rows(page, 2024) == []
