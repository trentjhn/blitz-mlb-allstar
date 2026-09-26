import csv
import hashlib
import json
import logging
import os
from dataclasses import replace
from pathlib import Path

import pytest

import build
from allstar import config
from allstar.discover import allstar_url, league_url
from allstar.rows import table_labels
from allstar.site import SITE_COLUMNS
from allstar.validate import REQUIRED, key, roster_gaps, validate

GOLDEN = Path(__file__).parent / "fixtures" / "golden_rows.csv"
BR = "https://www.baseball-reference.com"
# The spec's required columns, written out here rather than taken from validate.py, so a
# column dropped from REQUIRED there fails a test here.
SPEC_COLUMNS = [
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
# The real function, kept before the fixture below replaces it for speed.
READ_TEAM_PAGES = build.team_pages


def remembered(parse):
    """A page parser that runs once per distinct page and arguments. The parsers are pure
    functions of their input, so this changes nothing but the time."""
    results = {}

    def parse_once(page, *args):
        key = (hashlib.sha256(page).digest(), *args)
        if key not in results:
            results[key] = parse(page, *args)
        return results[key]

    return parse_once


@pytest.fixture(scope="module", autouse=True)
def pages(team_pages):
    """These tests build many times. The team pages come parsed from the session, and every
    other page is parsed once; each build still reads and checks every player, Show and
    All-Star page it uses."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(build, "team_pages", lambda manifest: team_pages)
        for parser in ("read_player", "show_top100", "all_star_rosters"):
            patch.setattr(build, parser, remembered(getattr(build, parser)))
        yield team_pages


def stop_line(caplog):
    """The build's one "stopped:" line."""
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stopped:")]
    return line


def read(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def manifest_where(monkeypatch, url, **changes):
    """Make the build load a manifest whose entry for url has these fields changed."""
    real = build.load_manifest

    def changed(raw_dir):
        manifest = real(raw_dir)
        manifest[url] = {**manifest[url], **changes}
        return manifest

    monkeypatch.setattr(build, "load_manifest", changed)


def test_one_row_per_player_season_table_and_team(built):
    rows = read(built / "all_stars.csv")
    keys = [(r["player_id"], r["season_id"], r["stat_type"], r["team_id"]) for r in rows]
    assert len(keys) == len(set(keys)) == 260
    per_season = {s: sum(r["season_id"] == s for r in rows) for s in ("2024", "2025", "2026")}
    assert per_season == {"2024": 83, "2025": 89, "2026": 88}


def test_every_column_the_spec_names_is_present_and_required(built):
    with open(built / "all_stars.csv", newline="") as handle:
        header = next(csv.reader(handle))
    assert len(SPEC_COLUMNS) == len(set(SPEC_COLUMNS)) == 57
    assert [column for column in SPEC_COLUMNS if column not in header] == []
    assert sorted(REQUIRED) == sorted(SPEC_COLUMNS)


def test_the_csv_is_utf8_with_lf_line_endings(built):
    raw = (built / "all_stars.csv").read_bytes()
    raw.decode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_a_second_build_is_byte_identical(built, tmp_path):
    assert build.main() == 0
    for name in ("all_stars.csv", "gaps.csv", "data.js"):
        assert (tmp_path / name).read_bytes() == (built / name).read_bytes()


def test_the_rosters_leave_no_gaps(built):
    assert (built / "gaps.csv").read_bytes() == b"season_id,player_id,name,gap\n"


def test_a_traded_all_star_has_one_row_per_team_with_that_teams_split(built):
    rows = [
        r
        for r in read(built / "all_stars.csv")
        if r["player_id"] == "arraelu01" and r["season_id"] == "2024"
    ]
    assert sorted((r["team_id"], r["G"], r["OPS"], r["team_record"]) for r in rows) == [
        ("MIA", "33", ".719", "62-100"),
        ("SDP", "117", ".744", "93-69"),
    ]


def test_a_pitching_row_with_no_position_takes_the_players_other_table(built):
    rows = {
        (r["player_id"], r["season_id"], r["stat_type"]): r for r in read(built / "all_stars.csv")
    }
    assert rows[("castrwi01", "2024", "pitching")]["primary_position"] == "UT"
    assert rows[("mckinza01", "2025", "pitching")]["primary_position"] == "3B"
    assert rows[("scottta01", "2024", "pitching")]["primary_position"] == "P"


def test_show_fields_are_set_only_for_the_79_matches(built):
    rows = read(built / "all_stars.csv")
    matched = {r["player_id"] for r in rows if r["is_show_top100"] == "true"}
    assert len(matched) == 79
    judge = next(r for r in rows if r["player_id"] == "judgeaa01")
    assert (judge["show_rank"], judge["show_overall_rating"], judge["show_potential_grade"]) == (
        "1",
        "99",
        "A",
    )
    assert all(r["show_rank"] == "" for r in rows if r["is_show_top100"] == "false")


def test_rows_are_sorted_by_season_team_table_and_player(built):
    rows = read(built / "all_stars.csv")
    order = [(r["season_id"], r["team_id"], r["stat_type"], r["player_id"]) for r in rows]
    assert order == sorted(order)


def test_each_row_is_dated_by_its_team_page_and_names_its_sources(built):
    manifest = build.load_manifest(config.RAW_DIR)
    for row in read(built / "all_stars.csv"):
        assert row["scraped_at"] == manifest[row["source_team_url"]]["fetched_at"]
        assert row["source_team_url"].endswith(f"/teams/{row['team_id']}/{row['season_id']}.shtml")
        assert row["source_player_url"].endswith(f"/{row['player_id']}.shtml")


def test_three_rows_match_their_golden_copy_cell_for_cell(built):
    # Every cell, blanks included, of a batter, a closer and a position player's pitching line,
    # under the exact header. The values were checked against the cached pages when copied.
    with open(built / "all_stars.csv", newline="") as handle, open(GOLDEN, newline="") as golden:
        assert next(csv.reader(handle)) == next(csv.reader(golden))
    rows = {key(r): r for r in read(built / "all_stars.csv")}
    for expected in read(GOLDEN):
        assert rows[key(expected)] == expected, key(expected)


def test_the_report_breaks_down_each_seasons_rows(caplog):
    caplog.set_level(logging.INFO)
    assert build.main() == 0
    for line in (
        "2024: 83 rows = 76 All-Stars + 4 rows from players in both tables + 3 from trades; "
        "game roster 76; on the Show top 100: 42 of 76 (55%)",
        "2025: 89 rows = 81 All-Stars + 6 rows from players in both tables + 2 from trades; "
        "game roster 81; on the Show top 100: 47 of 81 (58%)",
        "2026: 88 rows = 77 All-Stars + 8 rows from players in both tables + 3 from trades; "
        "game roster 77; on the Show top 100: 35 of 77 (45%)",
        "Show top-100 matches: 79 of 177 All-Stars",
        "roster gaps: 0",
        "260 rows, 77 columns",
    ):
        assert line in caplog.text


def test_a_failed_check_writes_nothing(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(build, "validate", lambda *args: ["planted problem"])
    assert build.main() == 1
    assert "check failed: planted problem" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_a_page_missing_from_the_cache_stops_the_build(tmp_path, monkeypatch, caplog):
    real = build.load_manifest

    def without_judge(raw_dir):
        manifest = real(raw_dir)
        del manifest[f"{BR}/players/j/judgeaa01.shtml"]
        return manifest

    monkeypatch.setattr(build, "load_manifest", without_judge)
    assert build.main() == 1
    assert "judgeaa01.shtml is not in the cache; run python scrape.py first" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("url", "path"),
    [
        (f"{BR}/players/j/judgeaa01.shtml", "players/judgeaa01.shtml"),
        (config.SHOW_URL, "the_show/top-100-players.html"),
        (allstar_url(2025), "allstar/2025-allstar-game.shtml"),
    ],
)
def test_a_page_that_differs_from_its_manifest_entry_stops_the_build(
    tmp_path, monkeypatch, caplog, url, path
):
    # An edited page and an unchanged entry look the same to the build: the hashes differ.
    manifest_where(monkeypatch, url, sha256="0" * 64)
    assert build.main() == 1
    assert f"stopped: data/raw/{path} differs from its manifest entry" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("url", "path"),
    [
        (league_url(2024), r"seasons/2024\.shtml"),
        (f"{BR}/teams/NYY/2024.shtml", r"teams/NYY_2024\.shtml"),
    ],
)
def test_league_and_team_pages_are_checked_against_the_manifest_too(url, path):
    # The fixture above stands in for team_pages, so these call the real one.
    manifest = build.load_manifest(config.RAW_DIR)
    manifest[url] = {**manifest[url], "sha256": "0" * 64}
    with pytest.raises(ValueError, match=rf"{path} differs from its manifest entry"):
        READ_TEAM_PAGES(manifest)


def test_a_page_swapped_together_with_its_hash_is_still_refused(tmp_path, monkeypatch, caplog):
    # Soto's page listed under Judge's URL with Soto's hash: only its canonical link differs.
    judge = f"{BR}/players/j/judgeaa01.shtml"
    soto = (config.RAW_DIR / "players/sotoju01.shtml").read_bytes()
    manifest_where(monkeypatch, judge, sha256=hashlib.sha256(soto).hexdigest())
    real = build.page_for

    def swapped(url):
        return replace(real(url), path="players/sotoju01.shtml") if url == judge else real(url)

    monkeypatch.setattr(build, "page_for", swapped)
    assert build.main() == 1
    assert f"stopped: data/raw/players/sotoju01.shtml is not the page for {judge}" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_a_team_page_swapped_together_with_its_hash_is_still_refused(monkeypatch):
    # BOS's page listed under NYY's URL with BOS's hash: only its canonical link differs.
    nyy = f"{BR}/teams/NYY/2024.shtml"
    manifest = build.load_manifest(config.RAW_DIR)
    bos = (config.RAW_DIR / "teams/BOS_2024.shtml").read_bytes()
    manifest[nyy] = {**manifest[nyy], "sha256": hashlib.sha256(bos).hexdigest()}
    real = build.page_for

    def swapped(url):
        return replace(real(url), path="teams/BOS_2024.shtml") if url == nyy else real(url)

    monkeypatch.setattr(build, "page_for", swapped)
    with pytest.raises(ValueError, match=rf"teams/BOS_2024\.shtml is not the page for {nyy}"):
        READ_TEAM_PAGES(manifest)


def test_an_unreadable_page_stops_the_build(tmp_path, monkeypatch, caplog):
    real = build.page_for

    def lost(url):
        page = real(url)
        return replace(page, path="players/nosuchplayer.shtml") if "judgeaa01" in url else page

    monkeypatch.setattr(build, "page_for", lost)
    assert build.main() == 1
    assert "stopped: [Errno 2] No such file or directory" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "change", [{}, {"fetched_at": None}, {"fetched_at": ""}, {"fetched_at": 1}]
)
def test_a_team_page_without_a_fetch_time_stops_the_build(change):
    manifest = build.load_manifest(config.RAW_DIR)
    url = f"{BR}/teams/ARI/2024.shtml"
    entry = {name: value for name, value in manifest[url].items() if name != "fetched_at"}
    manifest[url] = {**entry, **change}
    with pytest.raises(ValueError, match=r"ARI/2024\.shtml: its manifest entry has no fetch time"):
        READ_TEAM_PAGES(manifest)


def test_the_page_path_comes_from_its_url_not_the_manifest(built, tmp_path, monkeypatch):
    # An entry pointing at another player's file changes nothing: Judge's own file is read.
    manifest_where(monkeypatch, f"{BR}/players/j/judgeaa01.shtml", path="players/sotoju01.shtml")
    assert build.main() == 0
    assert (tmp_path / "all_stars.csv").read_bytes() == (built / "all_stars.csv").read_bytes()


def drop_ops_plus(stats):
    return {name: value for name, value in stats.items() if name != "OPS+"}


def so_as_k(stats):
    return {("K" if name == "SO" else name): value for name, value in stats.items()}


def games_last(stats):
    return {**{name: value for name, value in stats.items() if name != "G"}, "G": stats["G"]}


@pytest.mark.parametrize(
    ("team", "relabel", "change"),
    [
        ("BOS", drop_ops_plus, "missing ['OPS+'], extra []"),
        ("BOS", so_as_k, "missing ['SO'], extra ['K']"),
        ("BOS", games_last, "reordered"),
        # The first page read: the odd one out is found by count, not by order.
        ("ARI", drop_ops_plus, "missing ['OPS+'], extra []"),
    ],
)
def test_team_tables_with_different_columns_stop_the_build(
    pages, tmp_path, monkeypatch, caplog, team, relabel, change
):
    url = f"{BR}/teams/{team}/2024.shtml"

    def changed(page):
        if page.url != url:
            return page
        rows = [
            replace(row, stats=relabel(row.stats)) if row.stat_type == "batting" else row
            for row in page.rows
        ]
        return replace(page, rows=rows)

    monkeypatch.setattr(build, "team_pages", lambda manifest: [changed(p) for p in pages])
    assert build.main() == 1
    assert (
        f"{team}/2024.shtml: its batting table's columns differ from the other pages' ({change})"
        in caplog.text
    )
    assert list(tmp_path.iterdir()) == []


def test_a_failed_temp_write_replaces_neither_file(tmp_path, monkeypatch, caplog):
    build.CSV_PATH.write_text("last good csv\n")
    build.GAPS_PATH.write_text("last good gaps\n")
    real = build.stage

    def disk_full_on_the_appendix(path, data):
        if path == build.GAPS_PATH:
            raise OSError(28, "No space left on device")
        return real(path, data)

    monkeypatch.setattr(build, "stage", disk_full_on_the_appendix)
    assert build.main() == 1
    assert "stopped: could not write the output: [Errno 28] No space left on device" in (
        caplog.text
    )
    assert build.CSV_PATH.read_text() == "last good csv\n"
    assert build.GAPS_PATH.read_text() == "last good gaps\n"
    # The CSV was staged before the failure; its temp file is gone too.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["all_stars.csv", "gaps.csv"]


def test_a_failed_rename_names_the_file_already_replaced(tmp_path, monkeypatch, caplog):
    build.CSV_PATH.write_text("last good csv\n")
    build.GAPS_PATH.write_text("last good gaps\n")
    real, targets = os.replace, []

    def second_rename_fails(src, dst):
        targets.append(dst)
        if len(targets) == 2:
            raise PermissionError(1, "Operation not permitted")
        real(src, dst)

    monkeypatch.setattr(build.os, "replace", second_rename_fails)
    assert build.main() == 1
    # The note names the CSV, the one file already new, and nothing else.
    assert stop_line(caplog).endswith(f"; already replaced: {build.shown(build.CSV_PATH)}")
    assert build.CSV_PATH.read_text() != "last good csv\n"
    assert build.GAPS_PATH.read_text() == "last good gaps\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["all_stars.csv", "gaps.csv"]


def test_a_failed_first_rename_replaces_nothing_and_names_nothing(tmp_path, monkeypatch, caplog):
    build.CSV_PATH.write_text("last good csv\n")
    build.GAPS_PATH.write_text("last good gaps\n")

    def rename_fails(src, dst):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(build.os, "replace", rename_fails)
    assert build.main() == 1
    assert (
        stop_line(caplog)
        == "stopped: could not write the output: [Errno 1] Operation not permitted"
    )
    assert build.CSV_PATH.read_text() == "last good csv\n"
    assert build.GAPS_PATH.read_text() == "last good gaps\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["all_stars.csv", "gaps.csv"]


def test_an_appendix_path_that_is_a_folder_leaves_the_new_csv_named(tmp_path, caplog):
    # A real failure of the second rename: the rename cannot put a file where a folder is.
    build.GAPS_PATH.mkdir()
    assert build.main() == 1
    assert stop_line(caplog).endswith(f"; already replaced: {build.shown(build.CSV_PATH)}")
    assert build.CSV_PATH.is_file() and build.GAPS_PATH.is_dir()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["all_stars.csv", "gaps.csv"]


def test_a_site_data_path_that_is_a_folder_leaves_both_new_files_named(built, tmp_path, caplog):
    # A real failure of the third rename: the CSV and the appendix are new by then.
    build.SITE_PATH.mkdir()
    assert build.main() == 1
    new = f"{build.shown(build.CSV_PATH)}, {build.shown(build.GAPS_PATH)}"
    assert stop_line(caplog).endswith(f"; already replaced: {new}")
    for name in ("all_stars.csv", "gaps.csv"):
        assert (tmp_path / name).read_bytes() == (built / name).read_bytes()
    assert build.SITE_PATH.is_dir()
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["all_stars.csv", "data.js", "gaps.csv"]


def test_the_csv_is_replaced_by_a_rename_not_rewritten_in_place(tmp_path):
    build.CSV_PATH.write_bytes(b"last good csv\n")
    inode = build.CSV_PATH.stat().st_ino
    with open(build.CSV_PATH, "rb") as old:
        assert build.main() == 0
        # A reader of the old file still sees it whole; a rewrite in place would change it.
        assert old.read() == b"last good csv\n"
    assert build.CSV_PATH.stat().st_ino != inode


def test_the_build_cleans_only_its_own_output_folder(tmp_path, monkeypatch):
    swept = []
    monkeypatch.setattr(build, "remove_partial_writes", lambda folder: swept.append(folder) or [])
    assert build.main() == 0
    assert swept == [tmp_path]


def test_a_roster_gap_is_reported_and_written(monkeypatch, caplog):
    real = build.all_star_rosters

    def with_a_ghost(page, season):
        spots = real(page, season)
        if season == 2024:
            spots = [*spots, replace(spots[0], player_id="ghostpl01", name="Ghost Player")]
        return spots

    monkeypatch.setattr(build, "all_star_rosters", with_a_ghost)
    caplog.set_level(logging.INFO)
    assert build.main() == 0
    gap = "on the game roster, no All-Star mark on a team page"
    # The season is named: 2026's line also says "game roster 77".
    line = "2024: 83 rows = 76 All-Stars + 4 rows from players in both tables + 3 from trades"
    assert f"{line}; game roster 77;" in caplog.text
    assert "roster gaps: 1" in caplog.text
    assert f"2024 Ghost Player (ghostpl01): {gap}" in caplog.text
    assert read(build.GAPS_PATH) == [
        {"season_id": "2024", "player_id": "ghostpl01", "name": "Ghost Player", "gap": gap}
    ]


def test_a_read_only_output_folder_stops_with_a_message(tmp_path, monkeypatch, caplog):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(build, "CSV_PATH", out / "all_stars.csv")
    monkeypatch.setattr(build, "GAPS_PATH", out / "gaps.csv")
    out.chmod(0o555)
    try:
        assert build.main() == 1
        assert "stopped: could not write the output: [Errno 13] Permission denied" in caplog.text
        assert list(out.iterdir()) == []
    finally:
        out.chmod(0o755)


def test_a_temp_file_left_by_a_killed_build_is_removed(tmp_path, caplog):
    leftover = tmp_path / ".all_stars.csv.x1y2z3.partial"
    leftover.write_text("half a csv")
    assert build.main() == 0
    assert not leftover.exists()
    assert "removed" in caplog.text and "left by an interrupted or running build" in caplog.text


def test_a_season_with_no_rows_fails_the_checks_cleanly(pages, monkeypatch, caplog):
    monkeypatch.setattr(
        build, "team_pages", lambda manifest: [p for p in pages if p.team.season != 2026]
    )
    assert build.main() == 1
    assert "2026: All-Stars on 0 teams, not all 30" in caplog.text


def test_a_bats_throws_mismatch_fails_the_build(tmp_path, monkeypatch, caplog):
    real = build.match_show

    def one_mismatch(players, show):
        matches, _ = real(players, show)
        return matches, ["judgeaa01 (Aaron Judge): Baseball Reference has R/R, the Show has L/R"]

    monkeypatch.setattr(build, "match_show", one_mismatch)
    assert build.main() == 1
    assert "check failed: bats/throws differ: judgeaa01" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def good(built):
    """The real built rows and columns, fresh for each test to change."""
    with open(built / "all_stars.csv", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames)


def row_of(rows, player_id, season="2024", stat_type="batting"):
    return next(
        r
        for r in rows
        if (r["player_id"], r["season_id"], r["stat_type"]) == (player_id, season, stat_type)
    )


def problems_after(good, pages, change):
    rows, columns = good
    change(rows, columns)
    return validate(rows, columns, table_labels(pages))


def test_the_built_rows_pass_every_check(good, pages):
    assert problems_after(good, pages, lambda rows, columns: None) == []


# The field lists are written out, not taken from validate.py, so dropping a field from a
# check there fails a test here.
@pytest.mark.parametrize("field", ["player_id", "team_id", "season_id", "stat_type", "full_name"])
def test_an_empty_key_or_name_fails_the_checks(good, pages, field):
    problems = problems_after(good, pages, lambda rows, columns: rows[0].update({field: ""}))
    assert any(f"empty {field}" in p for p in problems), problems


@pytest.mark.parametrize(
    "field",
    [
        *["stat_type", "season_id", "bats", "throws", "is_all_star", "is_show_top100"],
        *["birth_date", "debut_date", "scraped_at", "height_inches", "weight_lbs"],
        *["team_wins", "team_losses"],
    ],
)
def test_an_odd_value_fails_the_checks(good, pages, field):
    problems = problems_after(good, pages, lambda rows, columns: rows[0].update({field: "X"}))
    assert any(f"{field} is 'X'" in p for p in problems), problems


# The spec's counting stats per table, written out as above.
BATTING_COUNTS = ["G", "PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "CS", "BB", "SO"]
PITCHING_COUNTS = ["W", "L", "G", "GS", "GF", "CG", "SHO", "SV", "IP", "H", "R", "ER", "HR"]
PITCHING_COUNTS += ["BB", "SO"]


@pytest.mark.parametrize(
    ("player_id", "stat_type", "column"),
    [
        *[("judgeaa01", "batting", column) for column in BATTING_COUNTS],
        *[("yateski01", "pitching", column) for column in PITCHING_COUNTS],
    ],
)
def test_a_blank_counting_stat_fails_the_checks(good, pages, player_id, stat_type, column):
    def blank(rows, columns):
        row_of(rows, player_id, stat_type=stat_type)[column] = ""

    problems = problems_after(good, pages, blank)
    assert any(f"{column} is ''" in p for p in problems), problems


@pytest.mark.parametrize(
    ("player_id", "stat_type", "column", "value"),
    [
        ("judgeaa01", "batting", "HR", "58*"),
        ("judgeaa01", "batting", "OPS", "1.159*"),
        ("judgeaa01", "batting", "OPS", "1.2.3"),
        ("judgeaa01", "batting", "WAR", "x"),
        ("yateski01", "pitching", "ERA", "1.17*"),
    ],
)
def test_a_stat_that_is_not_a_number_fails_the_checks(
    good, pages, player_id, stat_type, column, value
):
    # A counting stat, the rates of both tables, and a column beyond the spec's minimum.
    def odd(rows, columns):
        row_of(rows, player_id, stat_type=stat_type)[column] = value

    problems = problems_after(good, pages, odd)
    assert any(f"{column} is {value!r}" in p for p in problems), problems


def test_throws_is_never_s(good, pages):
    # "Both" maps to S for bats only; a switch thrower would need a code the spec does not have.
    problems = problems_after(good, pages, lambda rows, columns: rows[0].update({"throws": "S"}))
    assert any("throws is 'S'" in p for p in problems), problems


def drop_column(rows, columns):
    columns.remove("OPS+")


def duplicate_a_row(rows, columns):
    rows.append(dict(rows[0]))


def wrong_record(rows, columns):
    rows[0]["team_record"] = "1-1"


def wrong_selections(rows, columns):
    rows[0]["all_star_selections_2024_2026"] = "9"


def show_true_with_a_blank_rank(rows, columns):
    next(r for r in rows if r["is_show_top100"] == "true")["show_rank"] = ""


def show_true_with_no_fields(rows, columns):
    next(r for r in rows if r["is_show_top100"] == "false")["is_show_top100"] = "true"


def show_false_with_every_field(rows, columns):
    next(r for r in rows if r["is_show_top100"] == "true")["is_show_top100"] = "false"


def show_false_with_a_rank(rows, columns):
    next(r for r in rows if r["is_show_top100"] == "false")["show_rank"] = "5"


def drop_a_team_season(rows, columns):
    rows[:] = [r for r in rows if (r["season_id"], r["team_id"]) != ("2024", "NYY")]


def change_a_spot_value(rows, columns):
    row_of(rows, "sotoju01")["HR"] = "40"


def change_a_spot_date(rows, columns):
    row_of(rows, "judgeaa01")["debut_date"] = "1992-04-26"


def drop_a_spot_row(rows, columns):
    rows.remove(row_of(rows, "sotoju01"))


def empty_a_position(rows, columns):
    rows[0]["primary_position"] = ""


def pitcher_position_for_a_hitter(rows, columns):
    row_of(rows, "judgeaa01")["primary_position"] = "P"


def two_names_for_one_team(rows, columns):
    row_of(rows, "judgeaa01")["team_name"] = "Yankees"


def one_name_for_two_teams(rows, columns):
    for row in rows:
        if (row["season_id"], row["team_id"]) == ("2024", "COL"):
            row["team_name"] = "Kansas City Royals"


def slash_date(rows, columns):
    rows[0]["birth_date"] = rows[0]["birth_date"].replace("-", "/")


def local_time(rows, columns):
    rows[0]["scraped_at"] = rows[0]["scraped_at"].rstrip("Z")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (drop_column, "missing column OPS+"),
        (duplicate_a_row, "appears 2 times"),
        (wrong_record, "disagrees with W-L"),
        (wrong_selections, "selections 9"),
        (show_true_with_a_blank_rank, "disagree with is_show_top100"),
        (show_true_with_no_fields, "disagree with is_show_top100"),
        (show_false_with_every_field, "disagree with is_show_top100"),
        (show_false_with_a_rank, "disagree with is_show_top100"),
        (drop_a_team_season, "2024: All-Stars on 29 teams"),
        (change_a_spot_value, "spot check ('sotoju01', '2024', 'batting', 'NYY')"),
        (change_a_spot_date, "spot check ('judgeaa01', '2024', 'batting', 'NYY')"),
        (drop_a_spot_row, "spot check ('sotoju01', '2024', 'batting', 'NYY')"),
        (empty_a_position, "empty primary_position"),
        (pitcher_position_for_a_hitter, "primary_position P, but the profile says 'Rightfielder'"),
        (two_names_for_one_team, "2024 NYY: more than one team_name"),
        (one_name_for_two_teams, "2024 Kansas City Royals: more than one team_id"),
        (slash_date, "birth_date is"),
        (local_time, "scraped_at is"),
    ],
)
def test_each_check_catches_its_problem(good, pages, change, message):
    problems = problems_after(good, pages, change)
    assert any(message in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("player_id", "stat_type", "column"),
    [("judgeaa01", "batting", "ERA"), ("yateski01", "pitching", "OPS")],
)
def test_a_value_in_the_other_tables_column_fails_the_checks(
    good, pages, player_id, stat_type, column
):
    def stray(rows, columns):
        row_of(rows, player_id, stat_type=stat_type)[column] = "1.00"

    problems = problems_after(good, pages, stray)
    assert any(f"values in another table's columns ['{column}']" in p for p in problems), problems


def test_roster_gaps_are_found_in_both_directions(good):
    rows, _ = good
    marked = {r["player_id"]: r["full_name"] for r in rows if r["season_id"] == "2024"}
    marked.pop("judgeaa01")
    roster = {**marked, "ghostpl01": "Ghost Player"}
    gaps = roster_gaps(rows, {2024: roster})
    assert [(g["player_id"], g["gap"]) for g in gaps] == [
        ("ghostpl01", "on the game roster, no All-Star mark on a team page"),
        ("judgeaa01", "marked on a team page, not on the game roster"),
    ]


def test_the_site_data_holds_every_row_as_the_csv_has_it(built):
    text = (built / "data.js").read_bytes().decode("ascii")
    prefix = "// Written by build.py from the CSV's rows.\nwindow.ALL_STARS = "
    assert text.startswith(prefix) and text.endswith(";\n")
    data = json.loads(text[len(prefix) : -2])
    rows = read(built / "all_stars.csv")
    assert data["asOf"] == max(row["scraped_at"] for row in rows) == "2026-09-25T10:51:12Z"
    assert data["rows"] == [{column: row[column] for column in SITE_COLUMNS} for row in rows]
