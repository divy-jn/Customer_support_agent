from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app import routes, supabase_auth
from app.config import settings
from app.main import app


EXCHANGE = "/api/auth/supabase/exchange"
CUSTOMER = {"id": 17, "name": "Gina", "email": "gina@example.test"}


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
        self.values = None
        self.columns = None

    def select(self, columns):
        self.columns = columns.split(",")
        return self

    def update(self, values):
        self.values = values
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def is_(self, key, value):
        assert value == "null"
        return self.eq(key, None)

    def limit(self, _count):
        return self

    def execute(self):
        matched = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters.items())]
        if self.values is not None:
            for row in matched:
                row.update(self.values)
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=[{key: row.get(key) for key in self.columns} for row in matched])


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setattr(settings, "debug_mode", False)
    monkeypatch.setattr(settings, "app_environment", "production")
    user = {
        "id": str(uuid4()), "email": CUSTOMER["email"],
        "email_confirmed_at": "2026-01-01T00:00:00Z", "user_metadata": {"role": "admin"},
    }
    rows = {"customers": [], "staff_accounts": []}
    table = Mock(side_effect=lambda name: Query(rows[name]))
    verifier = Mock(return_value=user)
    monkeypatch.setattr(supabase_auth, "verify_supabase_user", verifier)
    monkeypatch.setattr(supabase_auth, "supabase", SimpleNamespace(table=table))
    return SimpleNamespace(client=TestClient(app), user=user, rows=rows, verify=verifier, table=table)


def _exchange(auth, portal, **claims):
    return auth.client.post(
        EXCHANGE, headers={"Authorization": "Bearer supabase-valid-token"}, json={"portal": portal, **claims},
    )


def _payload(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return jwt.decode(response.json()["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def test_customer_token_is_bound_to_database_id(auth):
    auth.rows["customers"].append({**CUSTOMER, "supabase_user_id": auth.user["id"]})
    response = _exchange(auth, "customer", role="admin", customer_id=99, email="attacker@example.test")
    assert _payload(response)["sub"] == str(CUSTOMER["id"])
    assert _payload(response)["role"] == "customer"
    assert response.json()["principal"] == {**CUSTOMER, "role": "customer"}
    auth.verify.assert_called_once_with("supabase-valid-token")


def test_confirmed_email_links_only_an_unclaimed_customer(auth):
    auth.rows["customers"].append({**CUSTOMER, "supabase_user_id": None})
    response = auth.client.post(EXCHANGE, json={"access_token": "verified-token", "portal": "customer"})
    assert _payload(response)["sub"] == str(CUSTOMER["id"])
    assert auth.rows["customers"][0]["supabase_user_id"] == auth.user["id"]
    auth.user["id"] = str(uuid4())
    assert _exchange(auth, "customer").status_code == 403


@pytest.mark.parametrize("role", ["agent", "admin"])
def test_staff_role_comes_only_from_active_database_mapping(auth, role):
    auth.rows["staff_accounts"].append({
        "supabase_user_id": auth.user["id"], "role": role, "display_name": "Support", "is_active": True,
    })
    response = _exchange(auth, "staff", role="customer")
    assert _payload(response)["role"] == role
    assert _payload(response)["sub"] == auth.user["id"]
    assert _exchange(auth, "customer").status_code == 403
    auth.rows["staff_accounts"][0]["is_active"] = False
    assert _exchange(auth, "staff").status_code == 403


def test_unmapped_or_wrong_portal_is_forbidden(auth):
    assert _exchange(auth, "customer").status_code == 403
    auth.rows["customers"].append({**CUSTOMER, "supabase_user_id": None})
    assert _exchange(auth, "staff").status_code == 403
    assert auth.rows["customers"][0]["supabase_user_id"] is None


def test_unconfirmed_email_cannot_link_customer(auth):
    auth.rows["customers"].append({**CUSTOMER, "supabase_user_id": None})
    auth.user["email_confirmed_at"] = None
    assert _exchange(auth, "customer").status_code == 401
    auth.table.assert_not_called()


def test_invalid_supabase_token_fails_before_mapping(monkeypatch):
    monkeypatch.setattr(settings, "supabase_anon_key", "public-key")
    auth_get = Mock(return_value=httpx.Response(401))
    monkeypatch.setattr(supabase_auth.httpx, "get", auth_get)
    db = Mock()
    monkeypatch.setattr(supabase_auth, "supabase", db)
    response = TestClient(app).post(EXCHANGE, headers={"Authorization": "Bearer expired-token"})
    assert response.status_code == 401
    assert auth_get.call_args.args[0].endswith("/auth/v1/user")
    assert auth_get.call_args.kwargs["headers"]["apikey"] == "public-key"
    db.table.assert_not_called()


@pytest.mark.parametrize(("environment", "debug"), [("production", False), ("production", True), ("staging", True), ("development", False)])
def test_dev_login_needs_debug_and_local_environment(auth, monkeypatch, environment, debug):
    monkeypatch.setattr(settings, "app_environment", environment)
    monkeypatch.setattr(settings, "debug_mode", debug)
    assert auth.client.post("/api/v1/auth/dev-login?email=gina@example.test").status_code == 403
    assert auth.client.post("/api/v1/auth/agent/dev-login").status_code == 403


@pytest.mark.parametrize(("role", "directory", "own_history", "other_history"), [
    ("customer", 403, 200, 403), ("agent", 200, 200, 200), ("admin", 200, 200, 200),
    (None, 401, 401, 401),
])
def test_history_directory_and_search_authorization(auth, monkeypatch, role, directory, own_history, other_history):
    history = Mock(return_value='{"recent_tickets":[],"recent_orders":[]}')
    monkeypatch.setattr(routes, "get_customer_history", history)
    monkeypatch.setattr(routes, "get_all_customers", lambda **_: "[]")
    monkeypatch.setattr(routes, "lookup_customer", lambda _: "[]")
    headers = {}
    if role:
        token = jwt.encode(
            {"sub": "17", "role": role, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            settings.jwt_secret, algorithm=settings.jwt_algorithm,
        )
        headers["Authorization"] = f"Bearer {token}"
    assert auth.client.get("/api/v1/customers", headers=headers).status_code == directory
    assert auth.client.get("/api/v1/customers/search?q=Gina", headers=headers).status_code == directory
    assert auth.client.get("/api/v1/customers/17/history", headers=headers).status_code == own_history
    assert auth.client.get("/api/v1/customers/99/history", headers=headers).status_code == other_history
    assert [call.args[0] for call in history.call_args_list] == [
        id_ for id_, status in [(17, own_history), (99, other_history)] if status == 200
    ]
