from collections import Counter

import pytest

from allstar import config
from allstar.parse_allstar import all_star_rosters

GAME_2024 = (config.RAW_DIR / "allstar/2024-allstar-game.shtml").read_bytes()


def lineups_page(*tables):
    """A minimal game page: the lineups section inside a comment, as the site ships it."""
    section = '<div id="div_lineups">' + "".join(tables) + "</div>"
    return f'<html><body><div id="all_lineups"><!--{section}--></div></body></html>'.encode()


def roster(*rows, caption="NL All-Stars"):
    return f"<table><caption>{caption}</caption>{''.join(rows)}</table>"


def player(player_id, name, position=""):
    link = f'<a href="/players/{player_id[0]}/{player_id}.shtml">{name}</a>'
    return f"<tr><td></td><td>{link}</td><td>{position}</td></tr>"


def label(text):
    return f"<tr><td></td><td><strong>{text}</strong></td><td></td></tr>"


def test_both_full_rosters_come_from_the_lineups_section():
    spots = all_star_rosters(GAME_2024, 2024)
    assert Counter((s.league, s.role) for s in spots) == {
        ("NL", "starter"): 10,
        ("NL", "reserve"): 29,
        ("AL", "starter"): 10,
        ("AL", "reserve"): 27,
    }
    assert len({s.player_id for s in spots}) == len(spots) == 76


def test_starters_reserves_and_players_who_did_not_play_are_all_listed():
    spots = {s.player_id: s for s in all_star_rosters(GAME_2024, 2024)}
    assert (spots["skenepa01"].league, spots["skenepa01"].role, spots["skenepa01"].position) == (
        "NL",
        "starter",
        "P",
    )
    assert (spots["judgeaa01"].name, spots["judgeaa01"].role) == ("Aaron Judge", "starter")
    # Betts was named to the team but hurt, so he is on the roster and not in the box score.
    assert spots["bettsmo01"].role == "reserve"


def test_managers_are_not_players():
    assert all(s.player_id != "lovulto01" for s in all_star_rosters(GAME_2024, 2024))


def test_a_page_without_the_lineups_section_is_an_error():
    with pytest.raises(ValueError, match="no lineups section"):
        all_star_rosters(b"<html><body>nothing here</body></html>", 2024)


def test_each_seasons_roster_matches_the_team_page_marks(cached_teams):
    marked = {season: set() for season in config.SEASONS}
    for _, team, rows in cached_teams:
        marked[team.season] |= {row.player_id for row in rows}
    for season in config.SEASONS:
        page = (config.RAW_DIR / f"allstar/{season}-allstar-game.shtml").read_bytes()
        assert {s.player_id for s in all_star_rosters(page, season)} == marked[season], season
    assert [len(marked[season]) for season in config.SEASONS] == [76, 81, 77]


def test_a_table_for_anything_but_a_league_roster_is_an_error():
    page = lineups_page(roster(player("coachax01", "A Coach", "C"), caption="Coaches"))
    with pytest.raises(ValueError, match="unknown league 'Coaches'"):
        all_star_rosters(page, 2024)


def test_a_player_shown_in_bold_is_still_on_the_roster():
    bold = player("ohtansh01", "<strong>Shohei Ohtani</strong>", "DH")
    spots = all_star_rosters(lineups_page(roster(bold)), 2024)
    assert [(s.player_id, s.name, s.role) for s in spots] == [
        ("ohtansh01", "Shohei Ohtani", "starter")
    ]


def test_an_unknown_roster_section_is_an_error():
    page = lineups_page(roster(player("ohtansh01", "Shohei Ohtani"), label("Coaches")))
    with pytest.raises(ValueError, match="unknown section 'coaches'"):
        all_star_rosters(page, 2024)


def test_managers_reserves_and_positions_are_read_by_section():
    manager = '<tr><td></td><td><a href="/managers/roberda07.shtml">Dave Roberts</a></td></tr>'
    rows = [
        player("ohtansh01", "Shohei Ohtani", "DH"),
        label("Manager"),
        manager,
        label("Reserves"),
        # An extra cell after the position: the position is the one after the name.
        player("bettsmo01", "Mookie Betts", "SS").replace("</tr>", "<td>note</td></tr>"),
    ]
    spots = all_star_rosters(lineups_page(roster(*rows)), 2024)
    assert [(s.player_id, s.role, s.position) for s in spots] == [
        ("ohtansh01", "starter", "DH"),
        ("bettsmo01", "reserve", "SS"),
    ]


@pytest.mark.parametrize("reserves", [label("Reserves").replace("strong", "b"), ""])
def test_a_player_under_the_manager_is_an_error(reserves):
    # With the Reserves label missing or not bold, the reserves would fall under the manager.
    rows = [label("Manager"), reserves, player("bettsmo01", "Mookie Betts", "SS")]
    with pytest.raises(ValueError, match="bettsmo01 is listed under the manager"):
        all_star_rosters(lineups_page(roster(*rows)), 2024)


def test_a_roster_table_with_an_empty_caption_is_an_error():
    page = lineups_page(roster(player("ohtansh01", "Shohei Ohtani"), caption=""))
    with pytest.raises(ValueError, match="unknown league ''"):
        all_star_rosters(page, 2024)


def test_a_player_listed_twice_is_an_error():
    rows = [player("ohtansh01", "Shohei Ohtani"), label("Reserves"), player("ohtansh01", "Ohtani")]
    with pytest.raises(ValueError, match="ohtansh01 is listed twice"):
        all_star_rosters(lineups_page(roster(*rows)), 2024)


def test_a_name_with_non_breaking_spaces_reads_as_plain_words():
    spots = all_star_rosters(
        lineups_page(roster(player("delacel01", "Elly&nbsp;De&nbsp;La Cruz"))), 2024
    )
    assert spots[0].name == "Elly De La Cruz"
