import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import testdb


@pytest.fixture
def db():
    """DSN of a throwaway schema-loaded database on the local Postgres."""
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")
    with testdb.throwaway_db() as dsn:
        yield dsn
