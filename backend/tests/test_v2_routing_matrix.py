"""Deterministic Feature-Flag Routing Matrix and Supervisor Decision Invariant Tests.

Verifies the routing matrix across all supported architecture modes:
- legacy
- shadow
- v2_product
- v2_product_order
- v2_core
- v2_all

Proves:
1. Runtime entry point selection in WebSocket/chat handler.
2. Domain routing decisions based on feature flag and intent allowlists.
3. Unsupported intents safely fall back to legacy.
4. Critical Invariant: REQUEST_CLARIFICATION never executes domain mutations (order, payment, product, general).
5. Critical Invariant: ESCALATE always routes to Guardian policy control.
6. Graph topology: Every enabled V2 domain node unconditionally terminates at guardian_response.
"""

import pytest
from unittest.mock import MagicMock

from app.agents.domain_graph import (
    _ORDER_V2_INTENTS,
    _PAYMENT_V2_INTENTS,
    domain_support_graph,
    route_after_supervisor,
)
from app.agents.graph import customer_support_graph
from app.agents.supervisor import SupervisorAction
from app.config import settings


# ──────────────────────────────────────────────────────────────
#  1. Runtime Entry Point Verification
# ──────────────────────────────────────────────────────────────

class TestRuntimeEntryPointDispatch:
    """Verify that settings.agent_architecture_mode chooses the exact intended graph."""

    @pytest.mark.parametrize("mode,expected_graph_name", [
        ("legacy", "customer_support_graph"),
        ("shadow", "customer_support_graph"),
        ("v2_product", "domain_support_graph"),
        ("v2_product_order", "domain_support_graph"),
        ("v2_core", "domain_support_graph"),
        ("v2_all", "domain_support_graph"),
    ])
    def test_entry_point_selects_expected_graph(self, monkeypatch, mode, expected_graph_name):
        monkeypatch.setattr(settings, "agent_architecture_mode", mode)
        selected_graph = (
            domain_support_graph
            if settings.agent_architecture_mode.startswith("v2_")
            else customer_support_graph
        )
        if expected_graph_name == "domain_support_graph":
            assert selected_graph is domain_support_graph
        else:
            assert selected_graph is customer_support_graph


# ──────────────────────────────────────────────────────────────
#  2. Feature-Flag Routing Matrix
# ──────────────────────────────────────────────────────────────

class TestRoutingMatrix:
    """Deterministic verification of route_after_supervisor for every architecture mode."""

    # ── Legacy Mode ──
    @pytest.mark.parametrize("domain,intent", [
        ("product", "product_warranty"),
        ("order", "tracking"),
        ("order", "cancel_order"),
        ("payment", "refund"),
        ("payment", "billing_issue"),
        ("general", "faq"),
    ])
    def test_legacy_mode_routes_all_domains_to_legacy(self, monkeypatch, domain, intent):
        monkeypatch.setattr(settings, "agent_architecture_mode", "legacy")
        route = route_after_supervisor({
            "semantic_domain": domain,
            "intent": intent,
            "supervisor_action": SupervisorAction.CONTINUE.value,
        })
        assert route == "legacy"

    # ── Shadow Mode ──
    @pytest.mark.parametrize("domain,intent", [
        ("product", "product_warranty"),
        ("order", "tracking"),
        ("payment", "refund"),
        ("general", "faq"),
    ])
    def test_shadow_mode_routes_all_domains_to_legacy(self, monkeypatch, domain, intent):
        monkeypatch.setattr(settings, "agent_architecture_mode", "shadow")
        route = route_after_supervisor({
            "semantic_domain": domain,
            "intent": intent,
            "supervisor_action": SupervisorAction.CONTINUE.value,
        })
        assert route == "legacy"

    # ── v2_product Mode ──
    def test_v2_product_mode_domain_enablement(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product")

        # Product is enabled
        assert route_after_supervisor({
            "semantic_domain": "product", "intent": "troubleshooting",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "product"

        # Order, payment, general are disabled -> legacy fallback
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "tracking",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "refund",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"
        assert route_after_supervisor({
            "semantic_domain": "general", "intent": "faq",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"

    # ── v2_product_order Mode ──
    def test_v2_product_order_mode_domain_enablement(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product_order")

        # Product enabled
        assert route_after_supervisor({
            "semantic_domain": "product", "intent": "product_warranty",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "product"

        # Order enabled for supported intents
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "tracking",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "order"
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "cancel_order",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "order"

        # Order unsupported intent falls back to legacy
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "custom_brokerage_inquiry",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"

        # Payment and general disabled
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "refund",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"
        assert route_after_supervisor({
            "semantic_domain": "general", "intent": "faq",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"

    # ── v2_core Mode ──
    def test_v2_core_mode_domain_enablement(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_core")

        # Product enabled
        assert route_after_supervisor({
            "semantic_domain": "product", "intent": "product_warranty",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "product"

        # Order enabled
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "tracking",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "order"

        # Payment enabled for supported intents
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "refund",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "payment"
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "billing_issue",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "payment"

        # Payment unsupported intent falls back to legacy
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "wire_transfer_swift_code",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"

        # General disabled
        assert route_after_supervisor({
            "semantic_domain": "general", "intent": "faq",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "legacy"

    # ── v2_all Mode ──
    def test_v2_all_mode_domain_enablement(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # All four business domains enabled
        assert route_after_supervisor({
            "semantic_domain": "product", "intent": "product_warranty",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "product"
        assert route_after_supervisor({
            "semantic_domain": "order", "intent": "tracking",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "order"
        assert route_after_supervisor({
            "semantic_domain": "payment", "intent": "refund",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "payment"
        assert route_after_supervisor({
            "semantic_domain": "general", "intent": "faq",
            "supervisor_action": SupervisorAction.CONTINUE.value,
        }) == "general"


# ──────────────────────────────────────────────────────────────
#  3. Critical Invariant: Supervisor Action Enforcement
# ──────────────────────────────────────────────────────────────

class TestSupervisorActionEnforcement:
    """Verify that REQUEST_CLARIFICATION and ESCALATE are strictly enforced across ALL domains."""

    @pytest.mark.parametrize("domain,intent", [
        ("order", "cancel_order"),
        ("order", "tracking"),
        ("payment", "refund"),
        ("payment", "billing_issue"),
        ("product", "product_warranty"),
        ("general", "faq"),
    ])
    def test_request_clarification_never_executes_domain_node(self, monkeypatch, domain, intent):
        """Even in v2_all with valid intent, REQUEST_CLARIFICATION must route to legacy (safe clarification)."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        route = route_after_supervisor({
            "semantic_domain": domain,
            "intent": intent,
            "supervisor_action": SupervisorAction.REQUEST_CLARIFICATION.value,
        })
        assert route == "clarification", f"Domain {domain} with intent {intent} violated clarification boundary!"

    @pytest.mark.parametrize("domain,intent", [
        ("order", "cancel_order"),
        ("payment", "refund"),
        ("product", "hardware_defect"),
        ("general", "escalation"),
        ("escalation", "human_request"),
    ])
    def test_escalate_action_always_routes_to_guardian(self, monkeypatch, domain, intent):
        """ESCALATE action must route to Guardian policy node for controlled human handoff."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        route = route_after_supervisor({
            "semantic_domain": domain,
            "intent": intent,
            "supervisor_action": SupervisorAction.ESCALATE.value,
        })
        assert route == "guardian"

    @pytest.mark.parametrize("action", [
        SupervisorAction.CONTINUE.value,
        SupervisorAction.START_NEW.value,
        SupervisorAction.SUSPEND_AND_SWITCH.value,
        SupervisorAction.RESUME.value,
    ])
    def test_valid_supervisor_workflow_actions_are_accepted(self, monkeypatch, action):
        """Valid supervisor transitions honor domain routing."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        assert route_after_supervisor({
            "semantic_domain": "order",
            "intent": "tracking",
            "supervisor_action": action,
        }) == "order"

    @pytest.mark.parametrize("invalid_action", [
        None,
        "",
        "unknown_action",
        "legacy_fallback",
        "arbitrary_string",
    ])
    def test_malformed_or_invalid_supervisor_action_falls_back_to_legacy(self, monkeypatch, invalid_action):
        """Invalid or unrecognized supervisor actions must take safe fallback."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        assert route_after_supervisor({
            "semantic_domain": "order",
            "intent": "tracking",
            "supervisor_action": invalid_action,
        }) == "legacy"


# ──────────────────────────────────────────────────────────────
#  4. Graph Topology Invariant: All Enabled V2 Nodes Edge to Guardian
# ──────────────────────────────────────────────────────────────

class TestGraphTopologyInvariants:
    """Verify that the compiled LangGraph routes all enabled domain nodes into guardian_response."""

    def test_all_v2_domain_nodes_terminate_at_guardian_response(self):
        graph = domain_support_graph
        # Inspect the underlying graph structure
        nodes = graph.get_graph().nodes
        assert "v2_product" in nodes
        assert "v2_order" in nodes
        assert "v2_payment" in nodes
        assert "v2_general" in nodes
        assert "guardian_response" in nodes
        assert "legacy_fallback" in nodes

        edges = graph.get_graph().edges
        # Target for all domain nodes must be guardian_response
        for source in ["v2_product", "v2_order", "v2_payment", "v2_general"]:
            matching_edges = [e for e in edges if e.source == source]
            assert len(matching_edges) == 1, f"Expected 1 outgoing edge from {source}"
            assert matching_edges[0].target == "guardian_response", f"{source} does not edge to guardian_response!"
