import pytest

import app.main as main_mod
from app.main import validate_runtime_settings, warn_if_ha_unsafe_storage


def test_validate_runtime_settings_requires_secret_encryption_key(monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "SECRET_ENCRYPTION_KEY", None)
    with pytest.raises(RuntimeError):
        validate_runtime_settings()


def test_ha_guard_warns_on_local_storage(monkeypatch):
    """§6.2 deployment guard: local storage → a loud warning (not a hard failure,
    so a lone replica still boots)."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", None)
    monkeypatch.setattr(settings, "STORAGE_PROTOCOL", "local")
    assert settings.effective_storage_protocol == "local"

    warnings: list[str] = []
    monkeypatch.setattr(
        main_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a if a else msg)
    )
    warn_if_ha_unsafe_storage()  # must not raise

    assert any("replica" in w and "S3_ENDPOINT_URL" in w for w in warnings)


def test_ha_guard_silent_on_s3_storage(monkeypatch):
    """With S3 selected (S3_ENDPOINT_URL set), the guard stays quiet — replicas > 1
    is supported."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://seaweedfs:8333")
    assert settings.effective_storage_protocol == "s3"

    warnings: list[str] = []
    monkeypatch.setattr(main_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    warn_if_ha_unsafe_storage()

    assert warnings == []
