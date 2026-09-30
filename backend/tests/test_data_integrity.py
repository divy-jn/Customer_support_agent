import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.generate_indian_dataset import generate_full_dataset
from app.models import OrderStatus, TicketStatus, TicketType, TicketPriority
from app.tools import update_ticket, cancel_order, process_refund

class TestGeneratorInvariants:
    @pytest.fixture(scope="class")
    def dataset(self):
        return generate_full_dataset()
        
    def test_tracking_number_uniqueness(self, dataset):
        """Verify tracking number is a unique domain identifier."""
        orders = dataset["orders"]
        tracking_numbers = [o["tracking_number"] for o in orders]
        assert len(tracking_numbers) == len(set(tracking_numbers)), "Tracking numbers must be unique"
        
    def test_order_status_validity(self, dataset):
        """All order statuses must be valid OrderStatus enum values."""
        valid_statuses = {e.value for e in OrderStatus}
        for o in dataset["orders"]:
            assert o["status"] in valid_statuses, f"Invalid order status: {o['status']}"
            
    def test_ticket_enums_validity(self, dataset):
        """All ticket enums must be valid."""
        valid_statuses = {e.value for e in TicketStatus}
        valid_types = {e.value for e in TicketType}
        valid_priorities = {e.value for e in TicketPriority}
        
        for t in dataset["tickets"]:
            assert t["status"] in valid_statuses, f"Invalid ticket status: {t['status']}"
            assert t["type"] in valid_types, f"Invalid ticket type: {t['type']}"
            assert t["priority"] in valid_priorities, f"Invalid ticket priority: {t['priority']}"
            
    def test_ticket_references(self, dataset):
        """Tickets must reference valid orders."""
        order_count = len(dataset["orders"])
        for t in dataset["tickets"]:
            assert t["order_index"] is not None
            assert 0 <= t["order_index"] < order_count, f"Invalid order reference: {t['order_index']}"

class TestToolContracts:
    @patch("app.tools.supabase")
    def test_update_ticket_sets_updated_at(self, mock_supabase):
        mock_execute = MagicMock()
        mock_execute.execute.return_value = MagicMock(data=[{"id": 1}])
        mock_supabase.table().update().eq().execute = mock_execute.execute
        
        # Test basic update
        res = json.loads(update_ticket(ticket_id=1, resolution="Fixed"))
        assert res.get("status") == "success"
        
        # Verify updated_at was in the payload
        update_payload = mock_supabase.table().update.call_args[0][0]
        assert "resolution" in update_payload
        assert "updated_at" in update_payload
        
    @patch("app.tools.supabase")
    def test_update_ticket_closed_sets_closed_at(self, mock_supabase):
        mock_execute = MagicMock()
        mock_execute.execute.return_value = MagicMock(data=[{"id": 1}])
        mock_supabase.table().update().eq().execute = mock_execute.execute
        
        res = json.loads(update_ticket(ticket_id=1, status="closed"))
        assert res.get("status") == "success"
        
        update_payload = mock_supabase.table().update.call_args[0][0]
        assert update_payload["status"] == "closed"
        assert "closed_at" in update_payload
        assert "updated_at" in update_payload

    @patch("app.tools.supabase")
    def test_cancel_order_rejects_non_active(self, mock_supabase):
        # Mock order lookup returning non-active
        mock_select = MagicMock()
        mock_select.select().eq().execute.return_value = MagicMock(data=[{"id": 1, "status": "shipped"}])
        mock_supabase.table().select().eq().execute = mock_select.select().eq().execute
        
        res = json.loads(cancel_order(order_id=1))
        assert "error" in res
        assert "shipped" in res["error"].lower() or "cannot be cancelled" in res["error"].lower()

    @patch("app.tools.supabase")
    def test_process_refund_rejects_active(self, mock_supabase):
        # Mock order lookup returning active
        mock_select = MagicMock()
        mock_select.select().eq().execute.return_value = MagicMock(data=[{"id": 1, "status": "active"}])
        mock_supabase.table().select().eq().execute = mock_select.select().eq().execute
        
        res = json.loads(process_refund(order_id=1))
        assert "error" in res
        assert "active" in res["error"].lower() or "cannot process refund" in res["error"].lower()
