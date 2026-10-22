import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides):
    values = {
        "SUPABASE_URL": "https://ci-project.supabase.co",
        "SUPABASE_KEY": "unit-test-key-long-enough",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_jwt_secret_is_required_outside_debug_mode():
    with pytest.raises(ValidationError, match="JWT_SECRET must be set"):
        _settings(JWT_SECRET="", DEBUG_MODE=False)


def test_short_and_example_jwt_secrets_are_rejected_outside_debug_mode():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _settings(JWT_SECRET="short-secret", DEBUG_MODE=False)

    with pytest.raises(ValidationError, match="placeholder"):
        _settings(JWT_SECRET="your-secure-jwt-secret-key-change-me", DEBUG_MODE=False)


def test_development_mode_can_use_the_local_only_fallback():
    settings = _settings(JWT_SECRET="", DEBUG_MODE=True)
    assert settings.jwt_secret == "dev-secret-key"


def test_valid_strong_jwt_secret_is_accepted_outside_debug_mode():
    settings = _settings(
        JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
        DEBUG_MODE=False,
    )
    assert settings.jwt_secret == "unit-test-strong-secret-with-32-plus-characters"


def test_direct_postgres_urls_never_fall_back_to_supabase_api_key():
    settings = _settings(DATABASE_PASSWORD="")

    with pytest.raises(ValueError, match="DATABASE_PASSWORD is required"):
        _ = settings.database_url

    with pytest.raises(ValueError, match="DATABASE_PASSWORD is required"):
        _ = settings.database_url_sync


def test_direct_postgres_urls_use_only_explicit_database_password():
    settings = _settings(DATABASE_PASSWORD="explicit-db-password")

    assert "explicit-db-password" in settings.database_url
    assert "explicit-db-password" in settings.database_url_sync
    assert "unit-test-key-long-enough" not in settings.database_url
