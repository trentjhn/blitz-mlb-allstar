import hashlib
import json
import os
import re
import subprocess
import sys
from itertools import pairwise

import pytest
import requests

from allstar import config
from allstar.fetch import StopScrape, make_session
from allstar.pacing import RequestPacer
from tests.fakes import (
    OTHER_TEAM_URL,
    TEAM_PAGE,
    TEAM_URL,
    FakeResponse,
    make_fetcher,
    passing_page,
    team_page,
)


def quarantined(tmp_path, name="teams/NYY_2024.shtml"):
    """The newest quarantine record for a page: its body and its details."""
    folder = tmp_path / "quarantine" / name.rsplit("/", 1)[0]
    stem = name.rsplit("/", 1)[1]
    records = sorted(p for p in folder.glob(f"{stem}.*") if p.suffix != ".json")
    kept = records[-1]
    return kept.read_bytes(), json.loads(kept.with_name(kept.name + ".json").read_text())


def test_good_page_is_cached_with_its_manifest_entry(tmp_path, clock):
    fetcher, session = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE

    assert (tmp_path / "raw/teams/NYY_2024.shtml").read_bytes() == TEAM_PAGE
    manifest = json.loads((tmp_path / "raw/manifest.json").read_text())
    assert manifest[TEAM_URL] == {
        "path": "teams/NYY_2024.shtml",
        "fetched_at": "2026-09-25T04:00:00Z",
        "bytes": len(TEAM_PAGE),
        "sha256": hashlib.sha256(TEAM_PAGE).hexdigest(),
    }
    call = session.calls[0]
    assert (call["allow_redirects"], call["stream"], call["timeout"]) == (
        False,
        True,
        config.TIMEOUT_S,
    )


def test_cached_page_makes_no_request(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)

    second, session = make_fetcher(tmp_path, clock)
    with second:
        assert second.get(TEAM_URL) == TEAM_PAGE
    assert session.calls == []


def test_force_refetches_each_page_once_per_run_and_replaces_it(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)

    newer = team_page(extra=b"updated")
    forced, session = make_fetcher(
        tmp_path, clock, FakeResponse(200, newer), force=True, now=lambda: "2026-09-26T00:00:00Z"
    )
    with forced:
        forced.get(TEAM_URL)
        assert forced.get(TEAM_URL) == newer
    assert len(session.calls) == 1
    assert (tmp_path / "raw/teams/NYY_2024.shtml").read_bytes() == newer
    entry = json.loads((tmp_path / "raw/manifest.json").read_text())[TEAM_URL]
    assert entry["fetched_at"] == "2026-09-26T00:00:00Z"
    assert entry["sha256"] == hashlib.sha256(newer).hexdigest()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (team_page(filler=0), "expected at least"),
        (team_page()[:55_000], "cut off"),
        (TEAM_PAGE.replace(b"players_standard_pitching", b"other_table"), "missing expected"),
        (team_page(url=OTHER_TEAM_URL), "missing expected"),
    ],
    ids=["too-small", "cut-off", "missing-table", "another-teams-page"],
)
def test_page_that_fails_the_gate_is_quarantined_not_cached(tmp_path, clock, body, reason):
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(200, body))
    with fetcher, pytest.raises(StopScrape, match=reason):
        fetcher.get(TEAM_URL)

    assert not (tmp_path / "raw/teams/NYY_2024.shtml").exists()
    kept, details = quarantined(tmp_path)
    assert kept == body
    assert details["status"] == 200
    assert reason in details["reason"]


def test_forced_refetch_that_comes_back_cut_off_keeps_the_good_copy(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)

    forced, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE[:55_000]), force=True)
    with forced, pytest.raises(StopScrape, match="cut off"):
        forced.get(TEAM_URL)
    assert (tmp_path / "raw/teams/NYY_2024.shtml").read_bytes() == TEAM_PAGE


@pytest.mark.parametrize("status", [429, 403])
def test_refusal_stops_the_whole_run_without_a_retry(tmp_path, clock, status):
    fetcher, session = make_fetcher(
        tmp_path,
        clock,
        FakeResponse(status, b"slow down", {"Retry-After": "3600"}),
        FakeResponse(200, TEAM_PAGE),
    )
    with fetcher:
        with pytest.raises(StopScrape, match="rate limiting"):
            fetcher.get(TEAM_URL)
        with pytest.raises(StopScrape, match="already stopped"):
            fetcher.get(OTHER_TEAM_URL)
    assert len(session.calls) == 1
    body, details = quarantined(tmp_path)
    assert (body, details["status"], details["headers"]) == (
        b"slow down",
        status,
        {"Retry-After": "3600"},
    )


def test_refusal_whose_body_fails_to_arrive_still_stops(tmp_path, clock):
    cut = requests.exceptions.ChunkedEncodingError()
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(429, read_error=cut), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher, pytest.raises(StopScrape, match="HTTP 429"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [204, 206, 301, 302, 404])
def test_any_other_non_200_below_500_stops(tmp_path, clock, status):
    headers = {"Location": "https://www.baseball-reference.com/elsewhere"} if status < 400 else {}
    fetcher, session = make_fetcher(tmp_path, clock, FakeResponse(status, b"x", headers))
    with fetcher, pytest.raises(StopScrape, match=f"HTTP {status}"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == 1
    # One bad page is not a block: later runs may still send.
    assert not (tmp_path / ".scrape.blocked").exists()
    assert quarantined(tmp_path)[1]["headers"] == headers


def test_server_error_is_retried_after_a_longer_wait(tmp_path, clock):
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(503), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE
    gap = session.calls[1]["at"] - session.calls[0]["at"]
    assert gap >= config.RETRY_WAITS_S[0] >= config.REQUEST_SPACING_S


@pytest.mark.parametrize(
    "error",
    [requests.Timeout(), requests.ConnectionError(), requests.exceptions.ChunkedEncodingError()],
    ids=["timeout", "connection", "dropped"],
)
def test_network_errors_are_retried(tmp_path, clock, error):
    fetcher, session = make_fetcher(tmp_path, clock, error, FakeResponse(200, TEAM_PAGE))
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE
    assert len(session.calls) == 2


def test_body_cut_off_mid_read_is_retried(tmp_path, clock):
    cut = requests.exceptions.ChunkedEncodingError()
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(200, read_error=cut), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE
    assert len(session.calls) == 2


def test_undecodable_body_stops_without_a_retry(tmp_path, clock):
    bad = requests.exceptions.ContentDecodingError("bad gzip")
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(200, read_error=bad), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher, pytest.raises(StopScrape, match=f"{TEAM_URL}: ContentDecodingError"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == 1


def test_gives_up_after_the_last_retry_and_keeps_the_last_response(tmp_path, clock):
    replies = [FakeResponse(503, b"down")] * (len(config.RETRY_WAITS_S) + 1)
    fetcher, session = make_fetcher(tmp_path, clock, *replies)
    with fetcher, pytest.raises(StopScrape, match="failed on all 3 attempts: HTTP 503, HTTP 503"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == len(replies)
    assert quarantined(tmp_path)[0] == b"down"


def test_requests_are_spaced_within_a_run(tmp_path, clock):
    fetcher, session = make_fetcher(
        tmp_path,
        clock,
        FakeResponse(200, TEAM_PAGE),
        FakeResponse(200, team_page(url=OTHER_TEAM_URL)),
    )
    with fetcher:
        fetcher.get(TEAM_URL)
        fetcher.get(OTHER_TEAM_URL)
    assert session.calls[1]["at"] - session.calls[0]["at"] >= config.REQUEST_SPACING_S


def test_offline_miss_stops_without_a_request(tmp_path, clock):
    fetcher, session = make_fetcher(tmp_path, clock, offline=True)
    with fetcher, pytest.raises(StopScrape, match="not in the cache"):
        fetcher.get(TEAM_URL)
    assert session.calls == []


def test_force_and_offline_cannot_be_combined(tmp_path, clock):
    with pytest.raises(ValueError):
        make_fetcher(tmp_path, clock, force=True, offline=True)


def test_get_outside_a_with_block_is_an_error(tmp_path, clock):
    fetcher, session = make_fetcher(tmp_path, clock)
    with pytest.raises(RuntimeError, match="with-statement"):
        fetcher.get(TEAM_URL)
    assert session.calls == []


def test_complete_page_left_unlisted_by_an_interrupted_run_is_kept(tmp_path, clock):
    page = tmp_path / "raw/teams/NYY_2024.shtml"
    page.parent.mkdir(parents=True)
    page.write_bytes(TEAM_PAGE)
    os.utime(page, (1_758_600_000, 1_758_600_000))
    fetcher, session = make_fetcher(tmp_path, clock)
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE
    assert session.calls == []
    entry = json.loads((tmp_path / "raw/manifest.json").read_text())[TEAM_URL]
    assert entry["fetched_at"] == "2025-09-23T04:00:00Z"


def test_incomplete_page_left_unlisted_is_quarantined_and_fetched_again(tmp_path, clock, caplog):
    page = tmp_path / "raw/teams/NYY_2024.shtml"
    page.parent.mkdir(parents=True)
    page.write_bytes(TEAM_PAGE[:55_000])
    fetcher, session = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with fetcher:
        assert fetcher.get(TEAM_URL) == TEAM_PAGE
    assert len(session.calls) == 1
    assert quarantined(tmp_path)[0] == TEAM_PAGE[:55_000]
    assert "NYY_2024.shtml to quarantine: an interrupted run left it incomplete" in caplog.text


def test_listed_page_that_went_missing_is_fetched_again(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)
    (tmp_path / "raw/teams/NYY_2024.shtml").unlink()

    offline, _ = make_fetcher(tmp_path, clock, offline=True)
    with offline, pytest.raises(StopScrape, match="not in the cache"):
        offline.get(TEAM_URL)
    again, session = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with again:
        assert again.get(TEAM_URL) == TEAM_PAGE
    assert len(session.calls) == 1


def test_edited_cached_page_stops_the_run_until_forced(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)
    page = tmp_path / "raw/teams/NYY_2024.shtml"
    page.write_bytes(TEAM_PAGE.replace(b"players", b"PLAYERS", 1))

    fetcher, session = make_fetcher(tmp_path, clock)
    with fetcher, pytest.raises(StopScrape, match="differs from the manifest"):
        fetcher.get(TEAM_URL)
    assert session.calls == []
    forced, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE), force=True)
    with forced:
        assert forced.get(TEAM_URL) == TEAM_PAGE


def test_partial_writes_left_by_a_crash_are_removed_on_open(tmp_path, clock):
    leftover = tmp_path / "raw/teams/.NYY_2024.shtml.k2j3h4.partial"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"half a page")
    keep = tmp_path / "raw/teams/NYY_2024.partial.notes"
    keep.write_bytes(b"not ours")
    fetcher, _ = make_fetcher(tmp_path, clock)
    with fetcher:
        pass
    assert not leftover.exists()
    assert keep.exists()


def test_user_agent_names_the_project_and_carries_no_email():
    agent = make_session().headers["User-Agent"]
    assert agent == config.USER_AGENT
    assert "blitz-mlb-allstar" in agent
    assert "@" not in agent


def test_every_request_in_a_run_is_spaced(tmp_path, clock):
    urls = [TEAM_URL, OTHER_TEAM_URL, "https://www.baseball-reference.com/teams/LAD/2024.shtml"]
    fetcher, session = make_fetcher(
        tmp_path, clock, *(FakeResponse(200, team_page(url=url)) for url in urls)
    )
    with fetcher:
        for url in urls:
            fetcher.get(url)
    starts = [call["at"] for call in session.calls]
    assert all(b - a >= config.REQUEST_SPACING_S for a, b in pairwise(starts))


def test_the_request_after_a_retry_is_still_spaced(tmp_path, clock):
    fetcher, session = make_fetcher(
        tmp_path,
        clock,
        FakeResponse(503),
        FakeResponse(200, TEAM_PAGE),
        FakeResponse(200, team_page(url=OTHER_TEAM_URL)),
    )
    with fetcher:
        fetcher.get(TEAM_URL)
        fetcher.get(OTHER_TEAM_URL)
    assert session.calls[2]["at"] - session.calls[1]["at"] >= config.REQUEST_SPACING_S
    assert fetcher.requests_made == 3


def test_spacing_and_timeout_meet_the_project_floor():
    assert config.REQUEST_SPACING_S >= 3.5
    assert config.TIMEOUT_S > 0


@pytest.mark.parametrize("status", [429, 403])
def test_a_refusal_blocks_later_requests_until_the_block_file_is_deleted(tmp_path, clock, status):
    cached, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with cached:
        cached.get(TEAM_URL)
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(status))
    with first, pytest.raises(StopScrape):
        first.get(OTHER_TEAM_URL)
    assert f"HTTP {status}" in (tmp_path / ".scrape.blocked").read_text()

    later, session = make_fetcher(tmp_path, clock, FakeResponse(200, team_page(url=OTHER_TEAM_URL)))
    with later:
        assert later.get(TEAM_URL) == TEAM_PAGE
        # The stop names the refused URL, read back from the block file.
        refused = rf"{re.escape(OTHER_TEAM_URL)}.*Delete .*scrape\.blocked"
        with pytest.raises(StopScrape, match=refused):
            later.get(OTHER_TEAM_URL)
    assert session.calls == []
    (tmp_path / ".scrape.blocked").unlink()
    again, session = make_fetcher(tmp_path, clock, FakeResponse(200, team_page(url=OTHER_TEAM_URL)))
    with again:
        again.get(OTHER_TEAM_URL)
    assert len(session.calls) == 1


def test_a_block_file_that_cannot_be_written_still_stops_cleanly(tmp_path, clock):
    (tmp_path / "not-a-folder").write_text("x")
    fetcher, _ = make_fetcher(
        tmp_path, clock, FakeResponse(429), block_path=tmp_path / "not-a-folder/.scrape.blocked"
    )
    with (
        fetcher,
        pytest.raises(StopScrape, match=r"rate limiting.*block file could not be written"),
    ):
        fetcher.get(TEAM_URL)
    assert quarantined(tmp_path)[1]["status"] == 429


def test_a_run_killed_while_a_refusal_arrives_still_blocks_the_next_run(tmp_path, clock):
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(429, read_error=KeyboardInterrupt()))
    with fetcher, pytest.raises(KeyboardInterrupt):
        fetcher.get(TEAM_URL)
    assert "HTTP 429" in (tmp_path / ".scrape.blocked").read_text()

    later, session = make_fetcher(tmp_path, clock)
    with later, pytest.raises(StopScrape, match="refused an earlier request"):
        later.get(OTHER_TEAM_URL)
    assert session.calls == []


def empty_file(path):
    path.write_bytes(b"")


def broken_symlink(path):
    path.symlink_to(path.parent / "missing.txt")


def live_symlink(path):
    (path.parent / "notes.txt").write_text("HTTP 429 from somewhere else")
    path.symlink_to(path.parent / "notes.txt")


def folder(path):
    path.mkdir()


def unreadable_file(path):
    path.write_text("HTTP 429")
    path.chmod(0)


def named_pipe(path):
    os.mkfifo(path)


STRAY = "is not a readable block record"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        # What a kill inside RequestPacer.block(), or a full disk, leaves behind.
        (empty_file, r"refused an earlier request \(no reason recorded\)"),
        (broken_symlink, STRAY),
        (live_symlink, STRAY),
        (folder, STRAY),
        pytest.param(
            unreadable_file,
            STRAY,
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file"),
        ),
        (named_pipe, STRAY),
    ],
)
def test_anything_at_the_block_path_blocks_requests(tmp_path, clock, entry, message):
    entry(tmp_path / ".scrape.blocked")
    fetcher, session = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with fetcher, pytest.raises(StopScrape, match=message) as stopped:
        fetcher.get(TEAM_URL)
    assert str(tmp_path / ".scrape.blocked") in str(stopped.value)
    assert session.calls == []
    assert not (tmp_path / "missing.txt").exists()


def test_only_the_start_of_a_long_block_record_is_read(tmp_path, clock):
    (tmp_path / ".scrape.blocked").write_text("HTTP 429 " + "x" * 3_000_000)
    fetcher, session = make_fetcher(tmp_path, clock)
    with fetcher, pytest.raises(StopScrape, match="refused an earlier request") as stopped:
        fetcher.get(TEAM_URL)
    assert len(str(stopped.value)) < 1_000
    assert session.calls == []


def test_a_failed_write_stops_the_run_without_a_second_request(tmp_path, clock, monkeypatch):
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(200, TEAM_PAGE), FakeResponse(200, TEAM_PAGE)
    )

    def full_disk(path, data):
        raise OSError(28, "No space left on device")

    with fetcher:
        monkeypatch.setattr("allstar.fetch.write_atomic", full_disk)
        with pytest.raises(StopScrape, match=r"writing teams/NYY_2024\.shtml failed.*No space"):
            fetcher.get(TEAM_URL)
        with pytest.raises(StopScrape, match="already stopped"):
            fetcher.get(TEAM_URL)
    assert len(session.calls) == 1


def test_partial_writes_are_only_removed_under_the_lock(tmp_path):
    leftover = tmp_path / "raw/teams/.NYY_2024.shtml.k2j3h4.partial"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"half a page")
    other = (
        "from pathlib import Path; from allstar.fetch import Fetcher\n"
        f"t = Path({str(tmp_path)!r})\n"
        "with Fetcher(raw_dir=t / 'raw', quarantine_dir=t / 'q', lock_path=t / '.scrape.lock',\n"
        "             block_path=t / '.blocked'): pass\n"
    )
    with RequestPacer(tmp_path / ".scrape.lock"):
        # Opened from a separate process with a timeout, so a lock that blocks instead of
        # refusing fails this test rather than hanging the suite.
        result = subprocess.run(
            [sys.executable, "-c", other],
            cwd=config.ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert "another scrape is running" in result.stderr
    assert leftover.exists()


def test_a_folder_named_like_a_partial_write_is_left_alone(tmp_path, clock):
    folder = tmp_path / "raw/teams/.odd.partial"
    folder.mkdir(parents=True)
    fetcher, _ = make_fetcher(tmp_path, clock)
    with fetcher:
        pass
    assert folder.is_dir()


def test_the_stop_latches_even_when_the_response_cannot_be_kept(tmp_path, clock):
    (tmp_path / "quarantine").write_text("a file where the folder should be")
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(429), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher:
        with pytest.raises(StopScrape, match="could not be kept"):
            fetcher.get(TEAM_URL)
        with pytest.raises(StopScrape, match="already stopped"):
            fetcher.get(OTHER_TEAM_URL)
    assert len(session.calls) == 1


def test_an_interrupted_forced_refetch_recovers_on_the_next_run(tmp_path, clock):
    first, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with first:
        first.get(TEAM_URL)

    def interrupted():
        raise KeyboardInterrupt

    newer = team_page(extra=b"updated")
    forced, _ = make_fetcher(tmp_path, clock, FakeResponse(200, newer), force=True, now=interrupted)
    with forced, pytest.raises(KeyboardInterrupt):
        forced.get(TEAM_URL)

    after, session = make_fetcher(tmp_path, clock)
    with after:
        assert after.get(TEAM_URL) == newer
    assert session.calls == []


def test_lowercase_header_names_are_kept_with_the_response(tmp_path, clock):
    headers = {"location": "https://www.baseball-reference.com/x", "retry-after": "60"}
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(301, b"", headers))
    with fetcher, pytest.raises(StopScrape):
        fetcher.get(TEAM_URL)
    assert quarantined(tmp_path)[1]["headers"] == headers


@pytest.mark.parametrize("status", [600, 999])
def test_statuses_outside_200_and_5xx_stop_without_a_retry(tmp_path, clock, status):
    fetcher, session = make_fetcher(tmp_path, clock, FakeResponse(status))
    with fetcher, pytest.raises(StopScrape, match=f"HTTP {status}"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == 1
    assert not (tmp_path / ".scrape.blocked").exists()


def test_the_failure_message_lists_every_attempt(tmp_path, clock):
    fetcher, _ = make_fetcher(
        tmp_path, clock, FakeResponse(503), requests.ConnectionError(), requests.ConnectionError()
    )
    with fetcher, pytest.raises(StopScrape, match="HTTP 503, ConnectionError, ConnectionError"):
        fetcher.get(TEAM_URL)


def test_a_refused_body_that_cannot_be_read_is_noted(tmp_path, clock):
    cut = requests.exceptions.ChunkedEncodingError()
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(429, read_error=cut))
    with fetcher, pytest.raises(StopScrape, match="body unreadable"):
        fetcher.get(TEAM_URL)


def test_a_request_error_that_is_not_transient_stops_without_a_retry(tmp_path, clock):
    fetcher, session = make_fetcher(
        tmp_path, clock, requests.exceptions.InvalidURL("bad"), FakeResponse(200, TEAM_PAGE)
    )
    with fetcher, pytest.raises(StopScrape, match="InvalidURL"):
        fetcher.get(TEAM_URL)
    assert len(session.calls) == 1


def test_a_failure_while_opening_releases_the_lock(tmp_path, clock):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "manifest.json").write_text("{ half")
    broken, _ = make_fetcher(tmp_path, clock)
    with pytest.raises(ValueError), broken:
        pass
    (raw / "manifest.json").write_text("{}")
    fixed, _ = make_fetcher(tmp_path, clock)
    with fixed:
        pass


def test_two_rejections_of_one_page_keep_both_records(tmp_path, clock):
    for body in (b"first", b"second"):
        fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(200, body))
        with fetcher, pytest.raises(StopScrape):
            fetcher.get(TEAM_URL)
    kept = sorted(
        p.read_bytes() for p in (tmp_path / "quarantine/teams").glob("*") if p.suffix != ".json"
    )
    assert kept == [b"first", b"second"]


def test_the_network_guard_stops_real_requests():
    with pytest.raises(RuntimeError, match="must not use the network"):
        requests.Session().get("http://127.0.0.1:9/")


def test_a_stopped_run_refuses_cached_pages_too(tmp_path, clock):
    cached, _ = make_fetcher(tmp_path, clock, FakeResponse(200, TEAM_PAGE))
    with cached:
        cached.get(TEAM_URL)
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(429))
    with fetcher:
        with pytest.raises(StopScrape):
            fetcher.get(OTHER_TEAM_URL)
        with pytest.raises(StopScrape, match="already stopped"):
            fetcher.get(TEAM_URL)


def test_an_interrupted_write_stops_the_run(tmp_path, clock, monkeypatch):
    fetcher, session = make_fetcher(
        tmp_path, clock, FakeResponse(200, TEAM_PAGE), FakeResponse(200, TEAM_PAGE)
    )

    def interrupted(path, data):
        raise KeyboardInterrupt

    with fetcher:
        monkeypatch.setattr("allstar.fetch.write_atomic", interrupted)
        with pytest.raises(KeyboardInterrupt):
            fetcher.get(TEAM_URL)
        with pytest.raises(StopScrape, match="already stopped"):
            fetcher.get(OTHER_TEAM_URL)
    assert len(session.calls) == 1


PLAYER = "https://www.baseball-reference.com/players/j/judgeaa01.shtml"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (passing_page(PLAYER).replace(b"Born:", b"Birth"), "missing expected"),
        (
            b'<html><head><link rel="canonical" href="' + PLAYER.encode() + b'" /></head>'
            b"<body>Born:</body></html>\n",
            "expected at least 50000",
        ),
        (
            passing_page("https://www.baseball-reference.com/players/s/sotoju01.shtml"),
            "missing expected",
        ),
    ],
    ids=["no-born", "too-small", "another-players-page"],
)
def test_a_player_page_that_fails_the_gate_is_not_cached(tmp_path, clock, body, reason):
    fetcher, _ = make_fetcher(tmp_path, clock, FakeResponse(200, body))
    with fetcher, pytest.raises(StopScrape, match=reason):
        fetcher.get(PLAYER)
    assert not (tmp_path / "raw/players/judgeaa01.shtml").exists()
