import os
import subprocess
import sys

import pytest

from allstar import config
from allstar.pacing import RequestPacer, StopScrape


def test_spacing_holds_across_separate_runs(tmp_path, clock):
    lock = tmp_path / ".scrape.lock"
    with RequestPacer(
        lock, block_path=tmp_path / ".blocked", clock=clock, sleep=clock.sleep
    ) as first:
        first.wait_turn()
    started = clock.now
    clock.now += 1.0

    with RequestPacer(
        lock, block_path=tmp_path / ".blocked", clock=clock, sleep=clock.sleep
    ) as second:
        second.wait_turn()
    assert clock.now - started == config.REQUEST_SPACING_S


def test_a_clock_that_jumps_back_waits_at_most_one_spacing(tmp_path, clock):
    lock = tmp_path / ".scrape.lock"
    with RequestPacer(
        lock, block_path=tmp_path / ".blocked", clock=clock, sleep=clock.sleep
    ) as pacer:
        pacer.wait_turn()
        clock.now -= 3_600
        before = clock.now
        pacer.wait_turn()
    assert clock.now - before == config.REQUEST_SPACING_S


def test_second_scrape_is_refused_while_the_lock_is_held(tmp_path):
    lock = tmp_path / ".scrape.lock"
    other = (
        "import sys; from pathlib import Path; from allstar.pacing import RequestPacer\n"
        f"with RequestPacer(Path({str(lock)!r})): pass\n"
    )
    with RequestPacer(lock):
        # A separate process, with a timeout, so a lock that blocks instead of refusing
        # fails this test rather than hanging the suite.
        result = subprocess.run(
            [sys.executable, "-c", other],
            cwd=config.ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert result.returncode != 0
    assert "another scrape is running" in result.stderr


def test_symlink_at_the_lock_path_is_refused_and_its_target_untouched(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("keep me")
    lock = tmp_path / ".scrape.lock"
    lock.symlink_to(target)

    with pytest.raises(StopScrape, match="cannot open the lock file"), RequestPacer(lock):
        pass
    assert target.read_text() == "keep me"


def test_the_block_is_never_written_through_a_symlink(tmp_path):
    target = tmp_path / "elsewhere.txt"
    (tmp_path / ".blocked").symlink_to(target)
    pacer = RequestPacer(tmp_path / ".scrape.lock", block_path=tmp_path / ".blocked")
    with pytest.raises(OSError):
        pacer.block("HTTP 429")
    assert not target.exists()


def test_waiting_needs_an_open_pacer(tmp_path):
    with pytest.raises(RuntimeError, match="not open"):
        RequestPacer(tmp_path / ".scrape.lock").wait_turn()


def test_a_lock_file_deleted_mid_run_stops_the_run(tmp_path, clock):
    lock = tmp_path / ".scrape.lock"
    with RequestPacer(
        lock, block_path=tmp_path / ".blocked", clock=clock, sleep=clock.sleep
    ) as pacer:
        pacer.wait_turn()
        lock.unlink()
        with pytest.raises(StopScrape, match="deleted during the run"):
            pacer.wait_turn()


def test_checking_a_block_leaves_no_file_open(tmp_path, clock):
    (tmp_path / ".blocked").write_text("HTTP 429")
    pacer = RequestPacer(
        tmp_path / ".scrape.lock", block_path=tmp_path / ".blocked", clock=clock, sleep=clock.sleep
    )
    with pacer:
        open_before = len(os.listdir("/dev/fd"))
        for _ in range(100):
            with pytest.raises(StopScrape):
                pacer.wait_turn()
        assert len(os.listdir("/dev/fd")) == open_before
