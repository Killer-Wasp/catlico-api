import pytest

from app.main import validate_runtime_settings


def test_validate_runtime_settings_requires_secret_encryption_key(monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "SECRET_ENCRYPTION_KEY", None)
    with pytest.raises(RuntimeError):
        validate_runtime_settings()
