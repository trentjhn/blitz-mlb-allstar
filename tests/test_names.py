import pytest

from allstar import config
from allstar.names import match_show, normalize
from allstar.parse_player import Player
from allstar.parse_show import ShowPlayer, show_top100


@pytest.mark.parametrize(
    ("name", "key"),
    [
        # The spec's own examples.
        ("José Ramírez", "jose ramirez"),
        ("Bobby Witt Jr.", "bobby witt"),
        ("Pete Crow-Armstrong", "pete crow armstrong"),
        ("Vladimir Guerrero Jr.", "vladimir guerrero"),
        ("Ryan O'Hearn", "ryan ohearn"),
        # A curly apostrophe (U+2019), as some sources print it.
        ("Ke" + chr(0x2019) + "Bryan Hayes", "kebryan hayes"),
        ("J.T. Realmuto", "jt realmuto"),
        ("Cal Ripken III", "cal ripken"),
        ("Ken Griffey Sr.", "ken griffey"),
        ("Ken Griffey II", "ken griffey"),
        # A non-breaking hyphen and an en dash read as the plain hyphen does.
        ("Pete Crow" + chr(0x2011) + "Armstrong", "pete crow armstrong"),
        ("Pete Crow" + chr(0x2013) + "Armstrong", "pete crow armstrong"),
        ("  Aaron   JUDGE ", "aaron judge"),
    ],
)
def test_names_normalize_as_the_spec_says(name, key):
    assert normalize(name) == key


def test_a_suffix_only_counts_at_the_end():
    assert normalize("Junior Caminero") == "junior caminero"
    assert normalize("Iván Herrera") == "ivan herrera"


SHOW = show_top100((config.RAW_DIR / "the_show/top-100-players.html").read_bytes())


def player(player_id, name, bats="R", throws="R"):
    return Player(
        player_id, name, "Pitcher", bats, throws, 72, 200, "2000-01-01", "Town, ST", "2020-01-01"
    )


def show_entry(name, rank=1, bats="R", throws="R"):
    return ShowPlayer(rank, name, "Team", "Pitcher", bats, throws, 90, "A")


def test_the_all_stars_on_the_show_list_are_found_with_matching_hands(cached_players):
    matches, mismatches = match_show(cached_players, SHOW)
    assert len(matches) == 79
    assert mismatches == []
    judge = matches["judgeaa01"]
    assert (judge.rank, judge.overall, judge.potential) == (1, 99, "A")


def test_an_all_star_off_the_list_is_not_matched(cached_players):
    matches, _ = match_show(cached_players, SHOW)
    assert not any(normalize(entry.name) == "andrew abbott" for entry in SHOW)
    assert "abbotan01" not in matches


def test_names_match_after_normalizing_both_sides():
    matches, _ = match_show(
        {"acunaro01": player("acunaro01", "Ronald Acuña Jr.")}, [show_entry("Ronald Acuna")]
    )
    assert "acunaro01" in matches


def test_two_all_stars_under_one_show_name_stop_the_join():
    # Baseball Reference has two Will Smiths: a catcher and a pitcher.
    all_stars = {pid: player(pid, "Will Smith") for pid in ("smithwi05", "smithwi04")}
    with pytest.raises(ValueError, match="smithwi04, smithwi05 all read as Will Smith"):
        match_show(all_stars, [show_entry("Will Smith")])


def test_two_show_entries_under_an_all_stars_name_stop_the_join():
    all_stars = {"ramirjo01": player("ramirjo01", "José Ramírez")}
    with pytest.raises(ValueError, match="José Ramírez and Jose Ramirez read as one name"):
        match_show(all_stars, [show_entry("José Ramírez"), show_entry("Jose Ramirez", rank=2)])


def test_two_show_entries_under_a_name_no_all_star_has_are_ignored():
    entries = [show_entry("José Ramírez"), show_entry("Jose Ramirez", rank=2)]
    assert match_show({"judgeaa01": player("judgeaa01", "Aaron Judge")}, entries) == ({}, [])


def test_different_hands_are_reported_and_the_match_is_kept():
    matches, mismatches = match_show(
        {"handssa01": player("handssa01", "Sam Hands", bats="L")}, [show_entry("Sam Hands")]
    )
    assert "handssa01" in matches
    assert mismatches == ["handssa01 (Sam Hands): Baseball Reference has L/R, the Show has R/R"]


def test_different_throws_alone_are_reported():
    matches, mismatches = match_show(
        {"handssa01": player("handssa01", "Sam Hands", throws="L")}, [show_entry("Sam Hands")]
    )
    assert "handssa01" in matches
    assert mismatches == ["handssa01 (Sam Hands): Baseball Reference has R/L, the Show has R/R"]
