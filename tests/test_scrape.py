import functools
import logging
import runpy
import signal
import sys

import pytest

import scrape
from allstar import config
from allstar.cache import load_manifest, save_manifest
from allstar.discover import allstar_url, league_url, team_urls
from allstar.fetch import Fetcher, StopScrape
from tests.fakes import FakeResponse, FakeSite, make_fetcher, passing_page


def team_codes(count):
    return [f"T{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(count)]


CODES = team_codes(30)


def league_page(season, codes=CODES):
    links = "".join(f'<a href="/teams/{code}/{season}.shtml">x</a>' for code in reversed(codes))
    return f"<html>{links}</html>".encode()


class ScriptedFetcher:
    """Answers get() from a dict and records the order; a missing URL stops the run."""

    def __init__(self, pages):
        self.pages = pages
        self.requests_made = 0
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise StopScrape(f"{url} is not in the cache")
        return self.pages[url]


def test_team_urls_come_from_the_real_2024_league_page():
    page = (config.RAW_DIR / "seasons/2024.shtml").read_bytes()
    urls = team_urls(page, 2024)
    assert len(urls) == 30
    assert f"{config.BR_BASE}/teams/OAK/2024.shtml" in urls
    assert f"{config.BR_BASE}/teams/TBR/2024.shtml" in urls


@pytest.mark.parametrize("count", [29, 31])
def test_a_league_page_without_exactly_30_teams_stops_the_run(count):
    with pytest.raises(ValueError, match=rf"links {count} teams.*seasons/2024\.shtml; delete it"):
        team_urls(league_page(2024, team_codes(count)), 2024)


def test_links_to_another_seasons_team_pages_are_ignored():
    page = league_page(2025) + b'<a href="/teams/OAK/2024.shtml">2024 Athletics</a>'
    assert team_urls(page, 2025) == [f"{config.BR_BASE}/teams/{code}/2025.shtml" for code in CODES]


def test_pages_are_fetched_in_the_planned_order(monkeypatch):
    monkeypatch.setattr(config, "SEASONS", (2024, 2025))
    pages = {config.SHOW_URL: b"show"}
    for season in config.SEASONS:
        pages[league_url(season)] = league_page(season)
        pages[allstar_url(season)] = b"game"
        for code in CODES:
            pages[f"{config.BR_BASE}/teams/{code}/{season}.shtml"] = b"team"
    fetcher = ScriptedFetcher(pages)

    handled = scrape.fetch_all(fetcher, report=lambda *args: None)

    teams = [f"{config.BR_BASE}/teams/{code}/{s}.shtml" for s in (2024, 2025) for code in CODES]
    assert (
        handled
        == fetcher.calls
        == [
            config.SHOW_URL,
            league_url(2024),
            league_url(2025),
            *teams,
            allstar_url(2024),
            allstar_url(2025),
        ]
    )
    assert len(set(handled)) == len(handled)


@pytest.fixture
def empty_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "QUARANTINE_DIR", tmp_path / "quarantine")
    monkeypatch.setattr(config, "LOCK_PATH", tmp_path / ".scrape.lock")
    monkeypatch.setattr(config, "BLOCK_PATH", tmp_path / ".scrape.blocked")
    return tmp_path


def whole_site():
    """Every page a full run requests, each good enough to pass the write gate."""
    pages = {config.SHOW_URL: passing_page(config.SHOW_URL)}
    for season in config.SEASONS:
        links = "".join(f'<a href="/teams/{code}/{season}.shtml">' for code in CODES)
        pages[league_url(season)] = passing_page(league_url(season), links.encode())
        pages[allstar_url(season)] = passing_page(allstar_url(season))
        for code in CODES:
            url = f"{config.BR_BASE}/teams/{code}/{season}.shtml"
            pages[url] = passing_page(url)
    return pages


@pytest.fixture
def site(empty_project, clock, monkeypatch):
    """scrape.main talks to a fake copy of every page, on a fake clock that never sleeps."""
    fake = FakeSite(whole_site())
    monkeypatch.setattr(
        scrape, "Fetcher", functools.partial(Fetcher, session=fake, clock=clock, sleep=clock.sleep)
    )
    return fake


@pytest.fixture
def full_cache(site):
    assert scrape.main([]) == 0
    site.calls.clear()
    return config.RAW_DIR


def test_a_full_run_requests_each_page_once_and_the_next_run_none(site, caplog):
    caplog.set_level(logging.INFO)
    assert scrape.main([]) == 0
    assert sorted(site.calls) == sorted(site.pages)
    site.calls.clear()

    assert scrape.main([]) == 0
    assert site.calls == []
    assert "done: 97 pages, 0 requests" in caplog.text


def test_force_requests_each_page_exactly_once(full_cache, site):
    assert scrape.main(["--force"]) == 0
    assert sorted(site.calls) == sorted(site.pages)


def test_check_cache_passes_on_a_full_cache_without_a_request(full_cache, site, caplog):
    caplog.set_level(logging.INFO)
    assert scrape.main(["--check-cache"]) == 0
    assert "cache complete: 97 pages" in caplog.text
    assert site.calls == []


def test_check_cache_reports_a_missing_page_instead_of_fetching_it(full_cache, site, caplog):
    url = f"{config.BR_BASE}/teams/{CODES[0]}/2025.shtml"
    manifest = load_manifest(full_cache)
    (full_cache / manifest.pop(url)["path"]).unlink()
    save_manifest(full_cache, manifest)

    assert scrape.main(["--check-cache"]) == 1
    assert f"{url} is not in the cache" in caplog.text
    assert site.calls == []


def snapshot(folder):
    return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def unlist_a_page(raw):
    manifest = load_manifest(raw)
    del manifest[f"{config.BR_BASE}/teams/{CODES[0]}/2025.shtml"]
    save_manifest(raw, manifest)
    return f"teams/{CODES[0]}_2025.shtml"


def leave_a_partial_write(raw):
    (raw / "teams/.TAA_2025.shtml.x1y2z3.partial").write_bytes(b"half a page")
    return "teams/.TAA_2025.shtml.x1y2z3.partial"


# A plain run would adopt the unlisted page or delete the partial write; a check only reports.
@pytest.mark.parametrize("damage", [unlist_a_page, leave_a_partial_write])
def test_check_cache_changes_nothing_even_when_it_fails(full_cache, site, caplog, damage):
    name = damage(full_cache)
    before = snapshot(full_cache)

    assert scrape.main(["--check-cache"]) == 1
    assert f"{name}: not in the manifest" in caplog.text
    assert snapshot(full_cache) == before


def test_check_cache_fails_on_a_damaged_page_the_run_never_reads(
    full_cache, site, empty_project, clock, caplog
):
    # A player page is outside this run, so only the manifest comparison can catch the damage.
    player = f"{config.BR_BASE}/players/j/judgeaa01.shtml"
    fetcher, _ = make_fetcher(empty_project, clock, FakeResponse(200, passing_page(player)))
    with fetcher:
        fetcher.get(player)
    (full_cache / "players/judgeaa01.shtml").write_bytes(b"damaged")

    assert scrape.main(["--check-cache"]) == 1
    assert "players/judgeaa01.shtml: contents differ from the manifest" in caplog.text


def test_a_damaged_manifest_stops_the_run_with_a_message(site, caplog):
    config.RAW_DIR.mkdir()
    (config.RAW_DIR / "manifest.json").write_text("{not json")
    assert scrape.main([]) == 1
    assert "stopped:" in caplog.text
    assert "is not valid JSON" in caplog.text
    assert site.calls == []


def test_a_failed_page_write_stops_the_run_naming_the_page(site, caplog):
    config.RAW_DIR.write_text("a file where the cache folder should be")
    assert scrape.main([]) == 1
    assert "stopped:" in caplog.text
    assert "writing the_show/top-100-players.html failed" in caplog.text
    assert "Not a directory" in caplog.text


def test_a_manifest_that_cannot_be_read_stops_the_run_with_a_message(site, caplog):
    (config.RAW_DIR / "manifest.json").mkdir(parents=True)
    assert scrape.main([]) == 1
    assert "stopped:" in caplog.text
    assert "manifest.json" in caplog.text
    assert site.calls == []


def test_an_interrupted_run_says_why_and_the_next_run_resumes(site, caplog, monkeypatch):
    serve = site.get

    def killed_after_ten(url, **kwargs):
        if len(site.calls) == 10:
            raise KeyboardInterrupt(signal.SIGTERM)  # what handle_signals makes of a kill
        return serve(url, **kwargs)

    monkeypatch.setattr(site, "get", killed_after_ten)
    assert scrape.main([]) == 128 + signal.SIGTERM
    assert "stopped by SIGTERM" in caplog.text

    monkeypatch.setattr(site, "get", serve)
    assert scrape.main([]) == 0
    assert sorted(site.calls) == sorted(site.pages)


def test_ctrl_c_stops_with_a_message_instead_of_a_traceback(site, caplog, monkeypatch):
    def ctrl_c(url, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(site, "get", ctrl_c)
    assert scrape.main([]) == 128 + signal.SIGINT
    assert "stopped by SIGINT" in caplog.text


@pytest.mark.parametrize(
    ("ignored", "handled"),
    [((), {signal.SIGTERM, signal.SIGHUP}), ((signal.SIGHUP,), {signal.SIGTERM})],
)
def test_kills_and_hangups_stop_the_run_unless_the_parent_ignored_them(
    monkeypatch, ignored, handled
):
    installed = {}
    monkeypatch.setattr(
        signal, "getsignal", lambda sig: signal.SIG_IGN if sig in ignored else signal.SIG_DFL
    )
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.update({sig: handler}))
    scrape.handle_signals()
    assert installed == dict.fromkeys(handled, scrape._stop_on_signal)


def test_a_signal_becomes_an_interrupt_that_carries_its_number():
    with pytest.raises(KeyboardInterrupt) as raised:
        scrape._stop_on_signal(signal.SIGTERM, None)
    assert raised.value.args == (signal.SIGTERM,)


def test_progress_is_logged_at_info(empty_project, monkeypatch):
    configured = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: configured.append(kwargs))
    scrape.main(["--check-cache"])
    assert [kwargs["level"] for kwargs in configured] == [logging.INFO]


def test_running_the_script_installs_the_signal_handlers(empty_project, monkeypatch):
    installed = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.append(sig))
    monkeypatch.setattr(sys, "argv", ["scrape.py", "--check-cache"])
    with pytest.raises(SystemExit):
        runpy.run_path(str(config.ROOT / "scrape.py"), run_name="__main__")
    assert signal.SIGTERM in installed


def test_offline_run_on_an_empty_cache_stops_without_a_request(empty_project, caplog):
    assert scrape.main(["--offline"]) == 1
    assert "not in the cache" in caplog.text


def test_check_cache_on_an_empty_cache_fails(empty_project, caplog):
    assert scrape.main(["--check-cache"]) == 1
    assert "manifest.json: missing" in caplog.text


def test_force_and_offline_are_mutually_exclusive(empty_project):
    with pytest.raises(SystemExit) as exit_info:
        scrape.main(["--force", "--offline"])
    assert exit_info.value.code == 2
