import json
from pathlib import Path

import pytest

from cashcycle.data import FixtureSource
from cashcycle.llm import StubLLM
from cashcycle.runway import compute_runway

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def expected():
    return json.loads((ROOT / "fixtures" / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture
def snap():
    return FixtureSource(ROOT / "fixtures" / "fixture.json").load()


@pytest.fixture
def runway(snap):
    return compute_runway(snap.cash)


@pytest.fixture
def stub():
    return StubLLM()
