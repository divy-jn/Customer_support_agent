"""Exchange a verified Supabase Auth session for an application token."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import httpx
import jwt
from fastapi import Header, HTTPException, Response
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.config import settings
from app.tools import supabase


class ExchangeRequest(BaseModel):
    access_token: str | None = Field(default=None, max_length=8192)
    portal: Literal["customer", "staff"] | None = None


def verify_supabase_user(access_token: str) -> dict:
    """Ask the project's Auth server to validate the access token."""
    if not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase sign-in is not configured")
    try:
        response = httpx.get(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
            headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {access_token}"},
            timeout=5.0,
        )
        if response.status_code in {400, 401, 403, 404, 422}:
            raise HTTPException(status_code=401, detail="Invalid or expired Supabase session")
        response.raise_for_status()
        user = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Supabase sign-in is unavailable") from exc
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Invalid Supabase user")
    return user


def _find_linked_customer(subject: str) -> dict | None:
    rows = (
        supabase.table("customers").select("id,name,email")
        .eq("supabase_user_id", subject).limit(1).execute().data
    )
    return rows[0] if rows else None


def _customer_for_user(subject: str, verified_email: str) -> dict | None:
    current = _find_linked_customer(subject)
    if current:
        return current

    # Never replace a link, and require the email to still match when writing.
    rows = (
        supabase.table("customers").select("id")
        .eq("email", verified_email).is_("supabase_user_id", "null").limit(1).execute().data
    )
    if not rows:
        return _find_linked_customer(subject)
    try:
        (
            supabase.table("customers").update({"supabase_user_id": subject})
            .eq("id", rows[0]["id"]).eq("email", verified_email)
            .is_("supabase_user_id", "null").execute()
        )
    except APIError as exc:
        if exc.code != "23505":
            raise
    # Re-read by subject to handle concurrent sign-ins without trusting a stale email lookup.
    return _find_linked_customer(subject)


def exchange_supabase_token(
    response: Response,
    body: ExchangeRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    token = body.access_token if body else None
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            token = None
    if not token or len(token) > 8192:
        raise HTTPException(status_code=401, detail="A Supabase access token is required")

    user = verify_supabase_user(token)
    try:
        subject = str(UUID(user["id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid Supabase user identity") from exc
    email = user.get("email")
    if not isinstance(email, str) or not email or not user.get("email_confirmed_at"):
        raise HTTPException(status_code=401, detail="A verified Supabase email is required")

    portal = body.portal if body else None
    principal = None
    app_subject = subject
    try:
        if portal != "customer":
            staff = (
                supabase.table("staff_accounts").select("role,display_name")
                .eq("supabase_user_id", subject).eq("is_active", True).limit(1).execute().data
            )
            if staff and staff[0]["role"] in {"agent", "admin"}:
                principal = {"role": staff[0]["role"], "name": staff[0]["display_name"], "email": email}
        if principal is None and portal != "staff":
            customer = _customer_for_user(subject, email)
            if customer:
                app_subject = str(customer["id"])
                principal = {**customer, "role": "customer"}
    except (APIError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail="Account lookup is unavailable") from exc
    if principal is None:
        raise HTTPException(status_code=403, detail="This account does not have access to the requested portal")

    expiration = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiration_minutes)
    app_token = jwt.encode(
        {"sub": app_subject, "role": principal["role"], "exp": expiration},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": app_token, "token_type": "bearer", "principal": principal}
