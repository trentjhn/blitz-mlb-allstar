import pytest

from allstar import config
from allstar.parse_show import ShowPlayer, show_top100

SHOW = (config.RAW_DIR / "the_show/top-100-players.html").read_bytes()


def test_the_list_has_every_rank_once_in_order():
    players = show_top100(SHOW)
    assert [player.rank for player in players] == list(range(1, 101))


def test_rank_one_is_aaron_judge_rated_99_a():
    assert show_top100(SHOW)[0] == ShowPlayer(
        rank=1,
        name="Aaron Judge",
        team="Yankees",
        position="Outfielder",
        bats="R",
        throws="R",
        overall=99,
        potential="A",
    )


def test_a_designated_hitter_is_read_though_his_position_link_is_empty():
    # A DH's position links to href="", unlike every other position on the list.
    alvarez = show_top100(SHOW)[38]
    assert (alvarez.name, alvarez.position, alvarez.bats, alvarez.throws) == (
        "Yordan Alvarez",
        "Designated Hitter",
        "L",
        "R",
    )


def test_the_ad_rows_between_players_are_skipped():
    assert SHOW.count(b"theshowratings_incontent_table_row") >= 5
    assert len(show_top100(SHOW)) == 100


def test_a_missing_rank_is_an_error():
    at = SHOW.index(b'<td class="counter">')
    end = SHOW.index(b"</tr>", at)
    page = SHOW[: SHOW.rindex(b"<tr", 0, at)] + SHOW[end + len(b"</tr>") :]
    with pytest.raises(ValueError, match="expected ranks 1-100"):
        show_top100(page)


def test_a_row_in_an_unknown_layout_is_an_error():
    page = SHOW.replace(b'<span title="Right/Right"> R/R</span>', b"", 1)
    with pytest.raises(ValueError, match="unknown layout"):
        show_top100(page)


def test_a_second_table_on_the_page_is_an_error():
    page = SHOW.replace(b"</body>", b"<table><tr><td>ad</td></tr></table></body>", 1)
    with pytest.raises(ValueError, match="expected one table, found 2"):
        show_top100(page)


def test_ranks_are_read_from_the_page_not_counted():
    # Swap the first two rank cells: counting rows would still give 1, 2, 3...
    first = b'<td class="counter">1.</td>'
    second = b'<td class="counter">2.</td>'
    assert first in SHOW and second in SHOW
    page = SHOW.replace(first, b"@@", 1).replace(second, first, 1).replace(b"@@", second, 1)
    with pytest.raises(ValueError, match="expected ranks 1-100"):
        show_top100(page)


def test_a_renamed_rating_column_is_an_error():
    page = SHOW.replace(
        b'title="Overall Rating">OVR</th>', b'title="Overall Rating">Overall</th>', 1
    )
    assert page != SHOW
    with pytest.raises(ValueError, match="unexpected columns"):
        show_top100(page)


JUDGE_ROW_END = b'<td><span class="attribute-box gold">A</span></td></tr>'
JUDGE_NAME = (
    b'<span class="entry-font"> <a href="https://www.theshowratings.com/aaron-judge">'
    b" Aaron Judge</a> </span>"
)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (JUDGE_ROW_END, b'<td><span class="attribute-box gold">A</span></td><td>x</td></tr>'),
        (JUDGE_ROW_END, b"<td></td></tr>"),
        (b'<span class="99.00 attribute-box dark-matter">99</span>', b"<span>ninety</span>"),
        (b'<span title="Right/Right"> R/R</span>', b'<span title="Both/Right"> B/R</span>'),
        (JUDGE_NAME, b'<span class="entry-font"> Aaron Judge </span>'),
        (
            JUDGE_NAME,
            JUDGE_NAME.replace(b" Aaron Judge</a>", b" </a>"),
        ),
        (
            b'<a href="https://www.theshowratings.com/teams/new-york-yankees"> Yankees</a>',
            b" Yankees",
        ),
        (JUDGE_ROW_END, b'<td><span class="attribute-box gold">G</span></td></tr>'),
        (b'<span class="99.00 attribute-box dark-matter">99</span>', b"<span>100</span>"),
    ],
    ids=[
        "an-extra-cell",
        "no-grade",
        "a-rating-in-words",
        "hands-b-r",
        "no-name-link",
        "an-empty-name",
        "no-team-link",
        "grade-g",
        "a-3-digit-rating",
    ],
)
def test_a_row_with_odd_cells_is_an_error(old, new):
    assert old in SHOW
    with pytest.raises(ValueError, match=r"row 1\. has an unknown layout"):
        show_top100(SHOW.replace(old, new, 1))


def test_the_last_rank_is_read_in_full():
    # A rating that reads differently backwards, unlike Judge's 99.
    assert show_top100(SHOW)[-1] == ShowPlayer(
        rank=100,
        name="James Wood",
        team="Nationals",
        position="Outfielder",
        bats="L",
        throws="R",
        overall=84,
        potential="A",
    )
