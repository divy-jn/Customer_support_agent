"""
Phase E.1 — Warranty Capability Tests

Tests check_warranty_status deterministic logic.
No DB or external services required.
Author: Madan <madan734895@gmail.com>
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import app.tools
from app.tools import check_warranty_status, _parse_warranty_duration
from app.models import WarrantyStatus, WarrantyStatusResult


# ──────────────────────────────────────────────
#  Time & Clock Management
# ──────────────────────────────────────────────

FIXED_NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)

def mock_clock():
    return FIXED_NOW


@pytest.fixture(autouse=True)
def inject_clock():
    # Inject deterministic clock into tools.py
    original = app.tools._CLOCK
    app.tools._CLOCK = mock_clock
    yield
    app.tools._CLOCK = original


# ──────────────────────────────────────────────
#  Database Mocks
# ──────────────────────────────────────────────

def create_mock_supabase(return_data=None, raise_exc=None):
    if return_data is None:
        return_data = []
        
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_eq2 = MagicMock()
    
    mock_supabase.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.eq.return_value = mock_eq
    mock_eq.eq.return_value = mock_eq2
    
    # Execution result
    mock_execute = MagicMock()
    if raise_exc:
        if isinstance(raise_exc, list):
            mock_execute.side_effect = raise_exc
        else:
            mock_execute.side_effect = raise_exc
    else:
        mock_execute.return_value = MagicMock(data=return_data)
        
    # Wire the execute method
    mock_eq2.execute = mock_execute
    mock_eq.execute = mock_execute
    mock_select.execute = mock_execute
    
    return mock_supabase


def build_mock_order(order_id=1, customer_id=10, name="SuperPhone X", desc="1-year warranty", date="2025-05-10T14:00:00Z"):
    return {
        "id": order_id,
        "customer_id": customer_id,
        "order_date": date,
        "products": {
            "id": 100 + order_id,
            "name": name,
            "description": desc
        }
    }


# ──────────────────────────────────────────────
#  Parsing Tests
# ──────────────────────────────────────────────

class TestWarrantyParser:
    def test_supported_year_format(self):
        delta = _parse_warranty_duration("This has a 1-year manufacturer warranty.")
        assert delta.years == 1
        assert delta.months == 0

    def test_supported_month_format(self):
        delta = _parse_warranty_duration("Limited 6-month warranty included.")
        assert delta.months == 6
        assert delta.years == 0

    def test_supported_space_separated_format(self):
        delta = _parse_warranty_duration("Comes with 2 year warranty.")
        assert delta.years == 2
        
        delta2 = _parse_warranty_duration("12 months warranty")
        assert (delta2.years * 12 + delta2.months) == 12

    def test_unsupported_format(self):
        delta = _parse_warranty_duration("Lifetime guarantee")
        assert delta is None

    def test_missing_warranty_text(self):
        delta = _parse_warranty_duration("Just a phone.")
        assert delta is None


# ──────────────────────────────────────────────
#  Capability Tests
# ──────────────────────────────────────────────

class TestWarrantyCapability:
    
    @patch("app.tools.supabase")
    def test_active_warranty(self, mock_supabase_global):
        # Bought 2026-05-10, 1-year warranty, current date 2026-09-15 -> ACTIVE
        mock_data = [build_mock_order(date="2026-05-10T14:00:00Z", desc="1-year warranty")]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, order_id=1)
        
        assert res.status == WarrantyStatus.ACTIVE
        assert res.eligible_purchase is True
        assert res.warranty_period == "1-year"
        assert res.warranty_expiry == datetime(2027, 5, 10, 14, 0, tzinfo=timezone.utc)

    @patch("app.tools.supabase")
    def test_expired_warranty(self, mock_supabase_global):
        # Bought 2025-05-10, 1-year warranty, current date 2026-09-15 -> EXPIRED
        mock_data = [build_mock_order(date="2025-05-10T14:00:00Z", desc="1-year warranty")]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, order_id=1)
        
        assert res.status == WarrantyStatus.EXPIRED

    @patch("app.tools.supabase")
    def test_exactly_at_expiry_boundary(self, mock_supabase_global):
        # Bought exactly 1 year ago relative to FIXED_NOW (2025-09-15T12:00:00Z)
        mock_data = [build_mock_order(date="2025-09-15T12:00:00Z", desc="1-year warranty")]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, order_id=1)
        
        # now < expiry is False (they are equal), so it's expired
        assert res.status == WarrantyStatus.EXPIRED

    @patch("app.tools.supabase")
    def test_no_matching_purchase(self, mock_supabase_global):
        mock_supabase_global.table = create_mock_supabase(return_data=[]).table
        res = check_warranty_status(customer_id=10, order_id=999)
        assert res.status == WarrantyStatus.NOT_FOUND

    @patch("app.tools.supabase")
    def test_invalid_customer(self, mock_supabase_global):
        res = check_warranty_status(customer_id=-5, order_id=1)
        assert res.status == WarrantyStatus.INVALID_CUSTOMER

    @patch("app.tools.supabase")
    def test_missing_product_name_and_order_id(self, mock_supabase_global):
        res = check_warranty_status(customer_id=10)
        assert res.status == WarrantyStatus.INVALID_REQUEST

    @patch("app.tools.supabase")
    def test_cross_customer_order_access_denied(self, mock_supabase_global):
        # Even if the DB mistakenly returns another customer's order, the capability must catch it
        mock_data = [build_mock_order(order_id=1, customer_id=11)]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        res = check_warranty_status(customer_id=10, order_id=1)
        assert res.status == WarrantyStatus.NOT_FOUND
        assert "not found or does not belong" in res.reason

    @patch("app.tools.supabase")
    def test_missing_warranty_text(self, mock_supabase_global):
        mock_data = [build_mock_order(desc="Refurbished product, no guarantees.")]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, order_id=1)
        assert res.status == WarrantyStatus.MISSING_DATA
        assert res.eligible_purchase is True  # The purchase itself is valid
        assert res.warranty_period is None
        assert res.warranty_expiry is None

    @patch("app.tools.supabase")
    def test_db_failure_sanitized(self, mock_supabase_global):
        # Always fails -> retries exhausted
        mock_supabase_global.table = create_mock_supabase(raise_exc=ConnectionError("DB Down")).table
        res = check_warranty_status(customer_id=10, order_id=1)
        assert res.status == WarrantyStatus.DB_ERROR
        assert "ConnectionError" in res.reason
        assert "DB Down" not in res.reason  # Sanitized
        assert res.reason == "Sanitized database error: ConnectionError"

    @patch("app.tools.supabase")
    def test_db_transient_failure_retries_and_succeeds(self, mock_supabase_global):
        # Fails twice with ConnectionError, succeeds on third
        success_data = MagicMock(data=[build_mock_order(date="2026-05-10T14:00:00Z", desc="1-year warranty")])
        mock_supabase_global.table = create_mock_supabase(
            raise_exc=[ConnectionError("Trans Fail 1"), ConnectionError("Trans Fail 2"), success_data]
        ).table
        
        # We need to temporarily speed up retries for the test to avoid 2+ second delays
        # but _supabase_retry doesn't easily let us configure sleep at runtime.
        # It's fast enough in test if mock doesn't sleep? Actually tenacity sleeps real time.
        # To avoid slow tests, we just mock asyncio.sleep or time.sleep
        import time
        with patch.object(time, "sleep", return_value=None):
            res = check_warranty_status(customer_id=10, order_id=1)
            
        assert res.status == WarrantyStatus.ACTIVE
        assert res.eligible_purchase is True


# ──────────────────────────────────────────────
#  Ambiguity Tests
# ──────────────────────────────────────────────

class TestWarrantyAmbiguity:
    
    @patch("app.tools.supabase")
    def test_product_name_exact_match(self, mock_supabase_global):
        mock_data = [
            build_mock_order(order_id=1, name="SuperPhone X"),
            build_mock_order(order_id=2, name="SuperPhone Y")
        ]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, product_name="SuperPhone X")
        assert res.status == WarrantyStatus.EXPIRED  # Default mock date is 2025
        assert res.order_id == 1

    @patch("app.tools.supabase")
    def test_ambiguous_purchase_multiple_matches(self, mock_supabase_global):
        # Customer bought SuperPhone X twice
        mock_data = [
            build_mock_order(order_id=1, name="SuperPhone X"),
            build_mock_order(order_id=2, name="SuperPhone X Pro")
        ]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        # Both contain "SuperPhone X" in their lowercase name
        res = check_warranty_status(customer_id=10, product_name="SuperPhone X")
        assert res.status == WarrantyStatus.AMBIGUOUS
        assert "Multiple purchases match" in res.reason

    @patch("app.tools.supabase")
    def test_product_name_no_match(self, mock_supabase_global):
        mock_data = [
            build_mock_order(order_id=1, name="SuperPhone Y")
        ]
        mock_supabase_global.table = create_mock_supabase(return_data=mock_data).table
        
        res = check_warranty_status(customer_id=10, product_name="SuperPhone X")
        assert res.status == WarrantyStatus.NOT_FOUND
