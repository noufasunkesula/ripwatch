import pytest

from rw.common import metrics
from rw.common.config import get_settings


@pytest.fixture
def env(monkeypatch):
    """Set environment variables and reload settings; restored after the test."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    for key in (
        "AWS_PROFILE",
        "RW_RUNTIME",
        "AWS_LAMBDA_FUNCTION_NAME",
        "RW_AWS_ENDPOINT_URL",
        "RW_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    metrics.reset()
    yield _set
    get_settings.cache_clear()
    metrics.reset()
