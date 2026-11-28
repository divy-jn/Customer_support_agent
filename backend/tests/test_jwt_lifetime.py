from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.config import Settings, settings
from app.dependencies import verify_customer_token


def test_jwt_lifetime_defaults_to_an_hour_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("JWT_EXPIRATION_MINUTES", raising=False)
    assert Settings(_env_file=None).jwt_expiration_minutes == 60
    monkeypatch.setenv("JWT_EXPIRATION_MINUTES", "90")
    assert Settings(_env_file=None).jwt_expiration_minutes == 90
    monkeypatch.setenv("JWT_EXPIRATION_MINUTES", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_existing_tokens_without_issuer_or_audience_keep_working():
    token = jwt.encode(
        {"sub": "7", "role": "customer", "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert verify_customer_token(credentials) == 7
