import pytest

from allstar import config
from allstar.parse_player import Player, read_player

PLACE = (
    b'  <span>\n    \n      in Sacramento, <a href="/bio/CA_born.shtml">CA</a>\n    \n  </span>\n'
)


def page(player_id):
    return (config.RAW_DIR / f"players/{player_id}.shtml").read_bytes()


def test_judges_profile():
    assert read_player(page("judgeaa01"), "judgeaa01") == Player(
        player_id="judgeaa01",
        full_name="Aaron Judge",
        position="Rightfielder",
        bats="R",
        throws="R",
        height_inches=79,
        weight_lbs=282,
        birth_date="1992-04-26",
        birth_place="Sacramento, CA",
        debut_date="2016-08-13",
    )


def test_a_foreign_born_player_keeps_his_country():
    acuna = read_player(page("acunaro01"), "acunaro01")
    assert (acuna.full_name, acuna.birth_place) == ("Ronald Acuña Jr.", "La Guaira, Venezuela")


def test_a_switch_hitter_bats_s():
    albies = read_player(page("albieoz01"), "albieoz01")
    assert (albies.bats, albies.throws) == ("S", "R")


def test_this_seasons_debut_is_read_from_plain_text():
    # 2026 debuts print the date as text; older ones link it.
    murakami = read_player(page("murakmu01"), "murakmu01")
    assert (murakami.debut_date, murakami.birth_place) == ("2026-03-26", "Kumamoto, Japan")


def test_every_cached_player_page_gives_a_complete_profile(cached_players):
    assert len(cached_players) == 177
    for player_id, player in cached_players.items():
        assert player.bats in "RLS" and player.throws in "RL", player_id
        assert 60 <= player.height_inches <= 84 and 140 <= player.weight_lbs <= 330, player_id
        assert len(player.birth_date) == len(player.debut_date) == 10, player_id
        assert ", " in player.birth_place and player.full_name, player_id


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (b"<strong>Bats: </strong>", b"<strong>Hits: </strong>", "no Bats: line"),
        (b"<strong>Bats: </strong>Right", b"<strong>Bats: </strong>Unknown", "no bats and throws"),
        (
            b"<strong>Throws: </strong>Right",
            b"<strong>Throws: </strong>Unknown",
            "no bats and throws",
        ),
        (b"282lb", b"282 lb", "no height, weight, birthplace or debut"),
        (b"August 13, 2016", b"TBD", "no height, weight, birthplace or debut"),
        (b"<h1>", b"<div>", "no profile header"),
        (b"&nbsp;&bull;&nbsp;", b" | ", "no bats and throws"),
        (b"<span>Aaron Judge</span>", b"<span></span>", "no name in the profile header"),
        # Without the place span only the country flag follows the date; it must not be read.
        (PLACE, b"", "no height, weight, birthplace or debut"),
        (b"      in Sacramento, ", b"      Sacramento, ", "no height, weight, birthplace or debut"),
        (b'id="meta"', b'id="other"', "no profile header"),
        (b'id="necro-birth"', b'id="birth"', "no height, weight, birthplace or debut"),
    ],
)
def test_a_profile_missing_a_fact_is_an_error(old, new, message):
    judge = page("judgeaa01")
    assert old in judge
    with pytest.raises(ValueError, match=f"judgeaa01: {message}"):
        read_player(judge.replace(old, new, 1), "judgeaa01")
