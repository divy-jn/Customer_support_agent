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
        # Mock the ownership/description lookup: table("tickets").select(...).eq(...).execute()
        mock_select_result = MagicMock()
        mock_select_result.data = [{"customer_id": None, "description": "Original description"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_select_result

        # Mock the RPC response: supabase.rpc("execute_ticket_mutation_with_outbox", ...).execute()
        rpc_result = MagicMock()
        rpc_result.data = {"status": "success", "ticket_id": 1, "updated_at": "2026-10-01T00:00:00Z"}
        mock_supabase.rpc.return_value.execute.return_value = rpc_result

        res = json.loads(update_ticket(ticket_id=1, resolution="Fixed", client_request_id="test-req"))
        assert res.get("status") == "success"

        # Verify the RPC was called with the correct function and payload
        rpc_call_args = mock_supabase.rpc.call_args
        assert rpc_call_args[0][0] == "execute_ticket_mutation_with_outbox"
        rpc_params = rpc_call_args[0][1]
        assert rpc_params["p_payload"]["resolution"] == "Fixed"
        assert rpc_params["p_client_request_id"] == "test-req"
        assert rpc_params["p_operation_type"] == "update_ticket"
        assert rpc_params["p_canonical_target"] == "1"

    @patch("app.tools.supabase")
    def test_update_ticket_closed_sets_closed_at(self, mock_supabase):
        # Mock the ownership/description lookup
        mock_select_result = MagicMock()
        mock_select_result.data = [{"customer_id": None, "description": "Original description"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_select_result

        # Mock the RPC response
        rpc_result = MagicMock()
        rpc_result.data = {"status": "success", "ticket_id": 1, "updated_at": "2026-10-01T00:00:00Z", "closed_at": "2026-10-01T00:00:00Z"}
        mock_supabase.rpc.return_value.execute.return_value = rpc_result

        res = json.loads(update_ticket(ticket_id=1, status="closed", client_request_id="test-req"))
        assert res.get("status") == "success"

        # Verify RPC payload includes the closed status
        rpc_call_args = mock_supabase.rpc.call_args
        assert rpc_call_args[0][0] == "execute_ticket_mutation_with_outbox"
        rpc_params = rpc_call_args[0][1]
        assert rpc_params["p_payload"]["status"] == "closed"
        # Verify outbox notification params are set for ticket closure
        assert rpc_params["p_outbox_notification_type"] == "TICKET_RESOLVED"

    @patch("app.tools.supabase")
    def test_cancel_order_rejects_non_active(self, mock_supabase):
        # Mock order lookup returning non-active
        mock_select = MagicMock()
        mock_select.select().eq().execute.return_value = MagicMock(data=[{"id": 1, "status": "shipped"}])
        mock_supabase.table().select().eq().execute = mock_select.select().eq().execute
        
        res = json.loads(cancel_order(order_id=1, client_request_id="test-req"))
        assert "error" in res
        assert "shipped" in res["error"].lower() or "cannot be cancelled" in res["error"].lower()

    @patch("app.tools.supabase")
    def test_process_refund_rejects_active(self, mock_supabase):
        # Mock order lookup returning active
        mock_select = MagicMock()
        mock_select.select().eq().execute.return_value = MagicMock(data=[{"id": 1, "status": "active"}])
        mock_supabase.table().select().eq().execute = mock_select.select().eq().execute
        
        res = json.loads(process_refund(order_id=1, client_request_id="test-req"))
        assert "error" in res
        assert "active" in res["error"].lower() or "cannot process refund" in res["error"].lower()
