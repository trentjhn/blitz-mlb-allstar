import pytest
import requests

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
