import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides):
    values = {
        "SUPABASE_URL": "https://ci-project.supabase.co",
        "SUPABASE_KEY": "unit-test-key-long-enough",
        # Do not inherit CI or developer-machine static credentials in config tests.
        "AGENT_SECRET": "",
        "ADMIN_API_KEY": "",
        "EMAIL_TEST_RECIPIENT": "",
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


def test_direct_postgres_urls_encode_special_password_characters():
    settings = _settings(DATABASE_PASSWORD="p@ss:word/path")

    assert "p%40ss%3Aword%2Fpath" in settings.database_url
    assert "p%40ss%3Aword%2Fpath" in settings.database_url_sync


def test_configured_static_agent_and_admin_secrets_must_be_strong_outside_debug_mode():
    with pytest.raises(ValidationError, match="AGENT_SECRET must be at least 32 characters"):
        _settings(
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            AGENT_SECRET="test-agent-secret",
            DEBUG_MODE=False,
        )

    with pytest.raises(ValidationError, match="ADMIN_API_KEY must be at least 32 characters"):
        _settings(
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            ADMIN_API_KEY="short-admin-key",
            DEBUG_MODE=False,
        )


def test_static_agent_secret_can_be_omitted_when_using_jwt_auth():
    settings = _settings(
        JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
        AGENT_SECRET="",
        DEBUG_MODE=False,
    )
    assert settings.agent_secret == ""


def test_staging_and_production_cannot_enable_debug_mode():
    strong_secret = "unit-test-strong-secret-with-32-plus-characters"
    for environment in ("staging", "production"):
        with pytest.raises(ValidationError, match="DEBUG_MODE must be false"):
            _settings(
                APP_ENVIRONMENT=environment,
                DEBUG_MODE=True,
                JWT_SECRET=strong_secret,
                DATABASE_PASSWORD="explicit-db-password",
            )


def test_production_requires_database_password_for_durable_workers():
    with pytest.raises(ValidationError, match="DATABASE_PASSWORD must be configured"):
        _settings(
            APP_ENVIRONMENT="production",
            DEBUG_MODE=False,
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            DATABASE_PASSWORD="",
        )


def test_production_accepts_explicit_strong_secrets_and_database_password():
    settings = _settings(
        APP_ENVIRONMENT="production",
        DEBUG_MODE=False,
        JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
        APPROVAL_SIGNING_SECRET="unit-test-dedicated-approval-secret-long-enough",
        SUPPORT_TEAM_EMAIL="support@support-operations.company",
        DATABASE_PASSWORD="explicit-db-password",
        AGENT_SECRET="",
        ADMIN_API_KEY="",
    )
    assert settings.app_environment == "production"
    assert settings.debug_mode is False


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_hosted_architecture_defaults_to_full_v2_and_allows_explicit_rollout(monkeypatch, environment):
    monkeypatch.delenv("AGENT_ARCHITECTURE_MODE", raising=False)
    values = dict(
        APP_ENVIRONMENT=environment, DEBUG_MODE=False,
        JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
        APPROVAL_SIGNING_SECRET="unit-test-dedicated-approval-secret-long-enough",
        SUPPORT_TEAM_EMAIL="support@support-operations.company",
        DATABASE_PASSWORD="explicit-db-password",
    )
    assert _settings(**values).agent_architecture_mode == "v2_all"
    assert _settings(**values, AGENT_ARCHITECTURE_MODE="v2_core").agent_architecture_mode == "v2_core"


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("recipient", ["", "  ", "support@example.com", "support@supportteam.com"])
def test_hosted_environments_require_real_support_email(environment, recipient):
    with pytest.raises(ValidationError, match="SUPPORT_TEAM_EMAIL"):
        _settings(
            APP_ENVIRONMENT=environment,
            DEBUG_MODE=False,
            DATABASE_PASSWORD="explicit-db-password",
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            APPROVAL_SIGNING_SECRET="unit-test-dedicated-approval-secret-long-enough",
            SUPPORT_TEAM_EMAIL=recipient,
        )


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_hosted_environments_reject_email_redirect(environment):
    with pytest.raises(ValidationError, match="EMAIL_TEST_RECIPIENT"):
        _settings(
            APP_ENVIRONMENT=environment,
            DEBUG_MODE=False,
            DATABASE_PASSWORD="explicit-db-password",
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            APPROVAL_SIGNING_SECRET="unit-test-dedicated-approval-secret-long-enough",
            EMAIL_TEST_RECIPIENT="test-inbox@example.com",
        )


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("secret", [
    "", "short-secret", "example-approval-secret-change-me-long-enough",
    "unit-test-strong-secret-with-32-plus-characters",
])
def test_hosted_environments_require_dedicated_approval_secret(environment, secret):
    with pytest.raises(ValidationError, match="APPROVAL_SIGNING_SECRET"):
        _settings(
            APP_ENVIRONMENT=environment,
            DEBUG_MODE=False,
            DATABASE_PASSWORD="explicit-db-password",
            JWT_SECRET="unit-test-strong-secret-with-32-plus-characters",
            APPROVAL_SIGNING_SECRET=secret,
        )
