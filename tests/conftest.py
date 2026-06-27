"""Pytest fixtures (if pytest is available). The actual loading lives in `_loaders.py` so the suite
also runs with plain `python3 tests/run.py` when pytest isn't installed."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _loaders import get_fixture  # noqa: E402


@pytest.fixture(scope="session")
def c_common():
    return get_fixture("c_common")


@pytest.fixture(scope="session")
def ti_core():
    return get_fixture("ti_core")


@pytest.fixture(scope="session")
def agent_mod():
    return get_fixture("agent_mod")
