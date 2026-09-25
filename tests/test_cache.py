import json

import pytest

from allstar import cache, config
from allstar.cache import check_cache, keep_rejected, load_manifest, write_atomic
from allstar.pages import page_for, rejection
from tests.fakes import OTHER_TEAM_URL, TEAM_PAGE, TEAM_URL, FakeResponse, make_fetcher, team_page


@pytest.mark.parametrize(
    ("url", "path"),
    [
        (config.SHOW_URL, "the_show/top-100-players.html"),
        ("https://www.baseball-reference.com/leagues/majors/2025.shtml", "seasons/2025.shtml"),
        (TEAM_URL, "teams/NYY_2024.shtml"),
        (
            "https://www.baseball-reference.com/allstar/2026-allstar-game.shtml",
            "allstar/2026-allstar-game.shtml",
        ),
        ("https://www.baseball-reference.com/players/j/judgeaa01.shtml", "players/judgeaa01.shtml"),
    ],
)
def test_cache_paths_come_from_the_url(url, path):
    assert page_for(url).path == path


@pytest.mark.parametrize(
    "url",
    [
        "https://www.baseball-reference.com/players/x/judgeaa01.shtml",
        "https://www.baseball-reference.com/teams/nyy/2024.shtml",
        "https://www.baseball-reference.com/teams/NYY/2024.shtml?redirect=1",
        "https://example.com/teams/NYY/2024.shtml",
    ],
)
def test_urls_outside_the_rules_are_refused(url):
    with pytest.raises(ValueError):
        page_for(url)


def test_every_cached_page_passes_its_own_gate():
    manifest = load_manifest(config.RAW_DIR)
    assert manifest, "the committed cache should not be empty"
    for url, entry in manifest.items():
        body = (config.RAW_DIR / entry["path"]).read_bytes()
        assert rejection(page_for(url), body) is None, url


@pytest.fixture
def two_cached_pages(tmp_path, clock):
    fetcher, _ = make_fetcher(
        tmp_path,
        clock,
        FakeResponse(200, TEAM_PAGE),
        FakeResponse(200, team_page(url=OTHER_TEAM_URL)),
    )
    with fetcher:
        fetcher.get(TEAM_URL)
        fetcher.get(OTHER_TEAM_URL)
    return tmp_path / "raw"


def test_check_cache_is_clean_after_fetching(two_cached_pages):
    assert check_cache(two_cached_pages) == []


def test_check_cache_reports_edits_renames_and_strays(two_cached_pages):
    raw = two_cached_pages
    page = raw / "teams/NYY_2024.shtml"
    page.write_bytes(TEAM_PAGE.replace(b"players", b"PLAYERS", 1))
    (raw / "teams/BOS_2024.shtml").rename(raw / "teams/bos_2024.shtml")
    (raw / "teams/extra.shtml").write_bytes(b"x")

    problems = "\n".join(check_cache(raw))
    assert "teams/NYY_2024.shtml: contents differ" in problems
    assert "teams/BOS_2024.shtml: missing, or its name differs in case" in problems
    assert "teams/extra.shtml: not in the manifest" in problems


def test_check_cache_reports_bad_manifest_entries(two_cached_pages):
    raw = two_cached_pages
    manifest = load_manifest(raw)
    manifest[TEAM_URL]["path"] = "teams/NYY_2025.shtml"
    del manifest[OTHER_TEAM_URL]["sha256"]
    manifest["https://example.com/page"] = {"path": "x", "fetched_at": "", "bytes": 0, "sha256": ""}
    (raw / "manifest.json").write_text(json.dumps(manifest))

    problems = "\n".join(check_cache(raw))
    assert "manifest path teams/NYY_2025.shtml should be teams/NYY_2024.shtml" in problems
    assert f"{OTHER_TEAM_URL}: manifest entry is missing fields" in problems
    assert "https://example.com/page: no cache rule" in problems


def test_check_cache_reports_a_missing_manifest(tmp_path):
    assert check_cache(tmp_path) == [f"{tmp_path / 'manifest.json'}: missing"]


def test_unreadable_manifest_names_its_file(tmp_path):
    (tmp_path / "manifest.json").write_text("{ half")
    with pytest.raises(ValueError, match=str(tmp_path / "manifest.json")):
        load_manifest(tmp_path)


def test_failed_write_keeps_the_old_file_and_leaves_no_partial(tmp_path, monkeypatch):
    target = tmp_path / "page.shtml"
    target.write_bytes(b"old")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["page.shtml"]


def test_written_files_are_readable_by_everyone(tmp_path):
    target = tmp_path / "page.shtml"
    write_atomic(target, b"x")
    assert target.stat().st_mode & 0o777 == 0o644


def test_check_cache_requires_every_manifest_field(two_cached_pages):
    raw = two_cached_pages
    manifest = load_manifest(raw)
    del manifest[TEAM_URL]["fetched_at"]
    (raw / "manifest.json").write_text(json.dumps(manifest))
    assert f"{TEAM_URL}: manifest entry is missing fields" in check_cache(raw)


def test_check_cache_ignores_folders(two_cached_pages):
    (two_cached_pages / "teams/archive").mkdir()
    assert check_cache(two_cached_pages) == []


@pytest.mark.parametrize("content", ["[]", '{"https://x": "not an entry"}'])
def test_a_manifest_of_the_wrong_shape_names_its_file(tmp_path, content):
    (tmp_path / "manifest.json").write_text(content)
    with pytest.raises(ValueError, match="is not a manifest"):
        load_manifest(tmp_path)


def test_quarantine_filters_headers_case_insensitively(tmp_path):
    kept = keep_rejected(
        tmp_path,
        TEAM_URL,
        "why",
        301,
        {"LOCATION": "x", "Set-Cookie": "y"},
        b"",
        "2026-09-25T04:00:00Z",
    )
    details = json.loads(kept.with_name(kept.name + ".json").read_text())
    assert details["headers"] == {"LOCATION": "x"}
    assert kept.name == "NYY_2024.shtml.20260925T040000Z"
