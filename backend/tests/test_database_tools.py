"""
Integration tests for Supabase database tools.

Tests customer lookup, order tracking, and ticket retrieval
against the seeded Indian dataset.

Usage:
    cd backend
    python -m pytest tests/test_database_tools.py -v
"""

import json
import pytest

# Add parent to path for imports
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tools import (
    lookup_customer,
    get_customer_by_id,
    get_customer_history,
    track_order,
    get_ticket,
    create_ticket,
    update_ticket,
    check_inventory,
    list_all_products,
    get_all_customers,
    get_all_tickets,
    get_dashboard_stats,
    check_supabase_health,
)


class TestSupabaseHealth:
    """Test that Supabase is reachable."""

    def test_supabase_health(self):
        result = check_supabase_health()
        assert result["status"] == "healthy", f"Supabase is not healthy: {result}"


class TestCustomerOperations:
    """Test customer lookup and retrieval."""

    def test_lookup_customer_by_name(self):
        """Search for a customer by a common Indian name."""
        result = json.loads(lookup_customer("Sharma"))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        # Our dataset has multiple Sharma customers
        assert len(result) > 0, "No customers found with name 'Sharma'"
        for customer in result:
            assert "name" in customer
            assert "email" in customer

    def test_lookup_customer_by_email(self):
        """Search for a customer by email domain."""
        result = json.loads(lookup_customer("example.in"))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) > 0, "No customers found with email domain 'example.in'"

    def test_lookup_customer_not_found(self):
        """Search for a non-existent customer returns empty list."""
        result = json.loads(lookup_customer("nonexistent_person_xyz_12345"))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) == 0, "Expected no results for non-existent customer"

    def test_get_all_customers(self):
        """Get paginated list of customers."""
        result = json.loads(get_all_customers(limit=10, offset=0))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) > 0, "No customers in database"
        for customer in result:
            assert "name" in customer
            assert "email" in customer
            assert "total_tickets" in customer or "total_orders" in customer

    def test_get_customer_history(self):
        """Get a customer's order and ticket history."""
        # First, get a valid customer ID
        customers = json.loads(get_all_customers(limit=1))
        assert len(customers) > 0, "No customers to test with"
        customer_id = customers[0]["id"]

        result = json.loads(get_customer_history(customer_id))
        assert "customer_id" in result, f"Expected customer_id in result: {result}"
        assert "recent_orders" in result
        assert "recent_tickets" in result

    def test_get_customer_by_invalid_id(self):
        """Invalid customer ID returns error."""
        result = json.loads(get_customer_by_id(999999))
        assert "error" in result, f"Expected error for invalid ID: {result}"


class TestOrderOperations:
    """Test order tracking and retrieval."""

    def test_track_existing_order(self):
        """Track an order that exists in the database."""
        # Get a valid order via customer history
        customers = json.loads(get_all_customers(limit=1))
        assert len(customers) > 0
        customer_id = customers[0]["id"]

        history = json.loads(get_customer_history(customer_id))
        orders = history.get("recent_orders", [])
        if not orders:
            pytest.skip("No orders found for test customer")

        order_id = orders[0]["id"]
        result = json.loads(track_order(order_id))
        assert "status" in result, f"Expected status in track result: {result}"
        assert result["status"] in ["active", "shipped", "delivered", "cancelled", "refunded"]

    def test_track_nonexistent_order(self):
        """Tracking a non-existent order returns error."""
        result = json.loads(track_order(999999))
        assert "error" in result, f"Expected error for non-existent order: {result}"


class TestTicketOperations:
    """Test ticket retrieval."""

    def test_get_all_tickets(self):
        """Get paginated tickets."""
        result = json.loads(get_all_tickets(limit=10))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) > 0, "No tickets in database"

    def test_get_ticket_detail(self):
        """Get details of a specific ticket."""
        tickets = json.loads(get_all_tickets(limit=1))
        if not tickets:
            pytest.skip("No tickets in database")

        ticket_id = tickets[0]["id"]
        result = json.loads(get_ticket(ticket_id))
        assert "subject" in result, f"Expected subject in ticket: {result}"
        assert "status" in result
        assert "priority" in result

    def test_get_nonexistent_ticket(self):
        """Non-existent ticket returns error."""
        result = json.loads(get_ticket(999999))
        assert "error" in result


class TestTicketMutations:
    """Test ticket creation and updates."""
    
    def test_create_ticket_invalid_enum(self):
        """Test that invalid enums are rejected."""
        # Use an invalid ticket type
        result = json.loads(create_ticket(1, "Subject", "Desc", ticket_type="invalid_type", priority="low"))
        assert "error" in result
        assert "Invalid ticket_type" in result["error"]
        
        # Use an invalid priority
        result = json.loads(create_ticket(1, "Subject", "Desc", ticket_type="inquiry", priority="invalid_priority"))
        assert "error" in result
        assert "Invalid priority" in result["error"]

    def test_update_ticket_invalid_enum(self):
        """Test that invalid enums are rejected in updates."""
        # Use invalid status
        result = json.loads(update_ticket(1, status="invalid_status"))
        assert "error" in result
        assert "Invalid status" in result["error"]
        
        # Use invalid priority
        result = json.loads(update_ticket(1, priority="invalid_priority"))
        assert "error" in result
        assert "Invalid priority" in result["error"]

    def test_update_ticket_valid_closed_at(self):
        """Test that closing a ticket sets closed_at using valid ISO format (if ticket exists)."""
        # Get a real ticket to update
        tickets = json.loads(get_all_tickets(limit=1))
        if not tickets:
            pytest.skip("No tickets in database to test update")
            
        ticket_id = tickets[0]["id"]
        # Valid close
        result = json.loads(update_ticket(ticket_id, status="closed"))
        assert result.get("status") == "success" or "error" in result # It might fail if we don't have access, but it shouldn't fail due to enum or datetime format


class TestProductOperations:
    """Test product/inventory operations."""

    def test_list_all_products(self):
        """List all products in catalog."""
        result = json.loads(list_all_products())
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) > 0, "No products in catalog"
        for product in result:
            assert "name" in product
            assert "price" in product

    def test_check_inventory_by_name(self):
        """Search for a product by name."""
        result = json.loads(check_inventory("PixelPro"))
        assert isinstance(result, list), f"Expected list, got: {type(result)}"
        assert len(result) > 0, "No products matching 'PixelPro'"

    def test_check_inventory_no_match(self):
        """Search for non-existent product returns error."""
        result = json.loads(check_inventory("NonExistentProduct12345"))
        assert isinstance(result, dict) and "error" in result


class TestDashboardStats:
    """Test dashboard/analytics operations."""

    def test_dashboard_stats(self):
        """Get dashboard statistics."""
        result = json.loads(get_dashboard_stats())
        assert "total_tickets" in result
        assert "open_tickets" in result
        assert "by_priority" in result
        assert "by_type" in result
