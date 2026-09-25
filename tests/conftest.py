import pytest
import requests

import build
from allstar import config
from allstar.cache import load_manifest
from allstar.parse_player import read_player
from tests.fakes import FakeClock


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test that forgets its fake session fails here instead of reaching the internet."""

    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(requests.Session, "send", refuse)


@pytest.fixture(autouse=True)
def outputs_in_tmp(tmp_path, monkeypatch):
    """No test writes the real data/output/, even one whose build should have stopped."""
    monkeypatch.setattr(build, "CSV_PATH", tmp_path / "all_stars.csv")
    monkeypatch.setattr(build, "GAPS_PATH", tmp_path / "gaps.csv")


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(scope="session")
def team_pages():
    """The team pages as the build reads them, parsed once per test session by its own code."""
    return build.team_pages(load_manifest(config.RAW_DIR))


@pytest.fixture(scope="session")
def cached_teams(team_pages):
    """Every team page as (url, team, All-Star rows), for the tests that check all of them."""
    return [(page.url, page.team, page.rows) for page in team_pages]


@pytest.fixture(scope="session")
def built(team_pages, tmp_path_factory):
    """One real build from the committed cache, once per test session, into a temp folder."""
    out = tmp_path_factory.mktemp("output")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(build, "team_pages", lambda manifest: team_pages)
        patch.setattr(build, "CSV_PATH", out / "all_stars.csv")
        patch.setattr(build, "GAPS_PATH", out / "gaps.csv")
        assert build.main() == 0
    return out


@pytest.fixture(scope="session")
def cached_players():
    """Every cached player page, parsed once: player id -> Player."""
    folder = config.RAW_DIR / "players"
    # "[!.]" skips hidden files, such as the "._" copies macOS leaves on some disks.
    pages = sorted(folder.glob("[!.]*.shtml"))
    return {path.stem: read_player(path.read_bytes(), path.stem) for path in pages}
