"""Settings validation — the SECRET_KEY production guard."""
import pytest

from app.core.configs import Settings

_DB = {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch):
    """An ambient SECRET_KEY (developer shell, CI) would count as explicitly
    provided and defeat the unset-key assertions."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)


def test_production_requires_explicit_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY must be explicitly set"):
        Settings(_env_file=None, ENVIRONMENT="production", **_DB)


def test_production_rejects_weak_secret_key():
    with pytest.raises(ValueError, match="too weak"):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY="changeme", **_DB)


def test_production_rejects_short_secret_key():
    with pytest.raises(ValueError, match="too weak"):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY="short", **_DB)


def test_production_accepts_strong_secret_key():
    key = "k" * 43  # token_urlsafe(32) length
    s = Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=key, **_DB)
    assert s.SECRET_KEY == key


def test_local_allows_generated_default():
    s = Settings(_env_file=None, ENVIRONMENT="local", **_DB)
    assert s.SECRET_KEY  # per-process random default is fine outside production
