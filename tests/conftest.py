import re

import pytest
import requests

from allstar import config
from allstar.cache import load_manifest
from allstar.parse_team import all_star_rows, read_team
from tests.fakes import FakeClock


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test that forgets its fake session fails here instead of reaching the internet."""

    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(requests.Session, "send", refuse)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(scope="session")
def cached_teams():
    """Every cached team page, parsed once for the tests that check all of them."""
    parsed = []
    for url, entry in sorted(load_manifest(config.RAW_DIR).items()):
        match = re.fullmatch(rf"{config.BR_BASE}/teams/([A-Z]{{3}})/(\d{{4}})\.shtml", url)
        if match:
            code, season = match.group(1), int(match.group(2))
            page = (config.RAW_DIR / entry["path"]).read_bytes()
            parsed.append((url, read_team(page, code, season), all_star_rows(page, season)))
    return parsed
