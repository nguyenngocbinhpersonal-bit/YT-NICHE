import pytest

from yt_niche.db.connection import connect


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()
