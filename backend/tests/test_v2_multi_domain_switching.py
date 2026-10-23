"""Deterministic Multi-Domain Context Switching and Supervisor State Integrity Tests.

Verifies:
1. All directed domain transitions:
   - Product <-> Order
   - Product <-> Payment
   - Order <-> Payment
2. Multi-turn roundtrip switching (Product -> Order -> Product) with state preserved.
3. Order tracking interrupted by an informational Product question, then resumed from typed state (no order_id in resumed text).
4. Pending refund/cancellation approval interrupted by an informational question (Product/General), surviving intact.
5. Ambiguous confirmation ("yes") after domain switch safely triggers clarification and blocks mutation.
6. Explicit confirmation resumes pending domain and executes mutation through trusted lifecycle.
7. Concurrent confirmation after resumption executes only once (CAS claim deduplication).
8. Multi-level suspension: a second and third domain switch while domains are already suspended.
9. Resumed domain metadata and clean cleanup without accumulating stale values.
10. Deduplication: repeated requests for the same action reuse existing PendingApproval UUID and operation_key.
11. Cross-customer and cross-session isolation.
12. Ambiguous multi-domain requests safely ask clarification without executing mutations or falling back to legacy.
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.approval_service import (
    create_pending_approval,
    compute_approval_signature,
    verify_approval_signature,
    validate_approval,
)
from app.agents.domain_graph import domain_support_graph
from app.agents.guardian import GuardianAction
from app.agents.supervisor import Supervisor, SupervisorAction
from app.config import settings
from app.models import (
    ApprovalStatus,
    DomainWorkflowStatus,
    GlobalWorkflowStatus,
    OrchestrationDomain,
    OrderState,
    PaymentState,
    PendingApproval,
    ProductState,
    RouterDiagnostics,
    RouterFailureType,
    SemanticRouteResult,
    ToolResultEnvelope,
    WorkflowState,
)
from app.websocket.chat_handler import handle_customer_ws
from app.persistence.session import session_store


@pytest.fixture(autouse=True)
def in_memory_session_store(monkeypatch):
    """Ensure tests run against in-memory session store rather than external network."""
    monkeypatch.setattr("app.persistence.session.redis_client", None)
    session_store.clear()


def _route(domain: str, intent: str, confidence: float = 0.98) -> SemanticRouteResult:
    return SemanticRouteResult(
        domain=domain,
        intent=intent,
        sentiment="neutral",
        urgency="medium",
        confidence=confidence,
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


# ──────────────────────────────────────────────────────────────
#  1. Directed Pair Transitions (Phase B & E)
# ──────────────────────────────────────────────────────────────

class TestDirectedDomainTransitions:
    """Verifies all directed transitions between Product, Order, and Payment."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_product_to_order_transition(
        self, mock_classify, mock_track, mock_cases, monkeypatch
    ):
        """1. Product -> Order: Product is suspended, Order tracks and stores order_id."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({"id": 1001, "status": "shipped", "products": {"name": "Laptop Stand"}})

        # Initial state in Product domain
        initial_ws = WorkflowState(
            session_id="sess-dir-1",
            customer_id=10,
            active_domain=OrchestrationDomain.PRODUCT.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_state=ProductState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                product_name="Pro Mechanical Keyboard",
            ),
        )

        state = {
            "session_id": "sess-dir-1",
            "customer_id": 10,
            "message": "Where is my order #1001?",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "order"
        assert res["supervisor_action"] == SupervisorAction.SUSPEND_AND_SWITCH.value
        assert ws["active_domain"] == OrchestrationDomain.ORDER.value
        assert OrchestrationDomain.PRODUCT.value in ws["suspended_domains"]
        assert ws["product_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert ws["product_state"]["product_name"] == "Pro Mechanical Keyboard"
        assert ws["order_state"]["order_id"] == "1001"
        assert ws["order_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        assert "shipped" in res["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_to_product_transition(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        """2. Order -> Product: Order is suspended with order_id preserved, Product executes."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {
            "response": "The headset carries a 2-year manufacturer warranty.",
            "escalated": False,
        }

        initial_ws = WorkflowState(
            session_id="sess-dir-2",
            customer_id=20,
            active_domain=OrchestrationDomain.ORDER.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            order_state=OrderState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                order_id="2002",
            ),
        )

        state = {
            "session_id": "sess-dir-2",
            "customer_id": 20,
            "message": "What is the warranty on the headset?",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "product"
        assert res["supervisor_action"] == SupervisorAction.SUSPEND_AND_SWITCH.value
        assert ws["active_domain"] == OrchestrationDomain.PRODUCT.value
        assert OrchestrationDomain.ORDER.value in ws["suspended_domains"]
        assert ws["order_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert ws["order_state"]["order_id"] == "2002"
        assert "2-year" in res["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_product_to_payment_transition(
        self, mock_classify, mock_cases, monkeypatch
    ):
        """3. Product -> Payment: Product is suspended, refund proposed without mutation."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("payment", "refund")

        initial_ws = WorkflowState(
            session_id="sess-dir-3",
            customer_id=30,
            active_domain=OrchestrationDomain.PRODUCT.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_state=ProductState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                product_name="Studio Monitor",
            ),
        )

        state = {
            "session_id": "sess-dir-3",
            "customer_id": 30,
            "message": "I need a refund for order #3003",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "payment"
        assert res["supervisor_action"] == SupervisorAction.SUSPEND_AND_SWITCH.value
        assert ws["active_domain"] == OrchestrationDomain.PAYMENT.value
        assert OrchestrationDomain.PRODUCT.value in ws["suspended_domains"]
        assert ws["product_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert res["pending_approval"] is not None
        assert res["pending_approval"]["action"] == "process_refund"
        assert res["pending_approval"]["params"]["order_id"] == 3003

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_payment_to_product_transition(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        """4. Payment -> Product: Payment and pending approval preserved, Product executed."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("product", "product_inquiry")
        mock_product.return_value = {
            "response": "The Studio Monitor supports HDMI and DisplayPort inputs.",
            "escalated": False,
        }

        existing_approval = create_pending_approval(
            action="process_refund",
            params={"order_id": 4004},
            customer_id=40,
            session_id="sess-dir-4",
            message="Shall I refund order #4004?",
        )

        initial_ws = WorkflowState(
            session_id="sess-dir-4",
            customer_id=40,
            active_domain=OrchestrationDomain.PAYMENT.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            payment_state=PaymentState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
            ),
        )

        state = {
            "session_id": "sess-dir-4",
            "customer_id": 40,
            "message": "What inputs does the monitor support?",
            "conversation_history": [],
            "pending_approval": existing_approval.model_dump(mode="json"),
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "product"
        assert ws["active_domain"] == OrchestrationDomain.PRODUCT.value
        assert OrchestrationDomain.PAYMENT.value in ws["suspended_domains"]
        assert ws["payment_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        # Pending approval must not be wiped or executed
        assert res["pending_approval"] is not None
        assert res["pending_approval"]["approval_id"] == existing_approval.approval_id

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_to_payment_transition(
        self, mock_classify, mock_cases, monkeypatch
    ):
        """5. Order -> Payment: Order is suspended with order_id, Payment proposes refund."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("payment", "refund")

        initial_ws = WorkflowState(
            session_id="sess-dir-5",
            customer_id=50,
            active_domain=OrchestrationDomain.ORDER.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            order_state=OrderState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                order_id="5005",
            ),
        )

        state = {
            "session_id": "sess-dir-5",
            "customer_id": 50,
            "message": "Actually, please refund order #5005",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "payment"
        assert ws["active_domain"] == OrchestrationDomain.PAYMENT.value
        assert OrchestrationDomain.ORDER.value in ws["suspended_domains"]
        assert ws["order_state"]["order_id"] == "5005"
        assert res["pending_approval"]["action"] == "process_refund"

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_payment_to_order_transition(
        self, mock_classify, mock_track, mock_cases, monkeypatch
    ):
        """6. Payment -> Order: Payment is suspended, Order tracks package."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({"id": 6006, "status": "out_for_delivery", "products": {"name": "Mic"}})

        initial_ws = WorkflowState(
            session_id="sess-dir-6",
            customer_id=60,
            active_domain=OrchestrationDomain.PAYMENT.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            payment_state=PaymentState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
            ),
        )

        state = {
            "session_id": "sess-dir-6",
            "customer_id": 60,
            "message": "Where is shipment for order #6006?",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "order"
        assert ws["active_domain"] == OrchestrationDomain.ORDER.value
        assert OrchestrationDomain.PAYMENT.value in ws["suspended_domains"]
        assert "out_for_delivery" in res["response"]


# ──────────────────────────────────────────────────────────────
#  2. State Resumption & Interruption Lifecycle (Phase C & E)
# ──────────────────────────────────────────────────────────────

class TestStateResumptionAndInterruption:
    """Verifies that suspended workflows resume seamlessly from typed state without re-prompting."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.general_agent.rag_agent.generate_response")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_interrupted_by_general_and_resumed_without_reprompt(
        self, mock_classify, mock_rag, mock_track, mock_cases, monkeypatch
    ):
        """Turn 1 Order -> Turn 2 General FAQ -> Turn 3 Resume Order using persisted order_id."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # ── Turn 1: Order tracking ──
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({"id": 7007, "status": "shipped", "tracking_number": "TRK-7007"})

        state_t1 = {
            "session_id": "sess-inter-1",
            "customer_id": 70,
            "message": "Where is my order #7007?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-inter-1", "customer_id": 70},
        }
        res_t1 = await domain_support_graph.ainvoke(state_t1)
        ws_t1 = res_t1["workflow_state"]
        assert ws_t1["active_domain"] == "order"
        assert ws_t1["order_state"]["order_id"] == "7007"

        # ── Turn 2: Unrelated General policy question (SUSPEND_AND_SWITCH) ──
        mock_classify.return_value = _route("general", "faq")
        mock_rag.return_value = {"response": "We accept returns within 30 days.", "sources": ["returns.md"], "context_used": True}

        state_t2 = {
            "session_id": "sess-inter-1",
            "customer_id": 70,
            "message": "What is your general return policy?",
            "conversation_history": [
                {"role": "customer", "content": state_t1["message"]},
                {"role": "agent", "content": res_t1["response"]},
            ],
            "workflow_state": ws_t1,
        }
        res_t2 = await domain_support_graph.ainvoke(state_t2)
        ws_t2 = res_t2["workflow_state"]
        assert ws_t2["active_domain"] == "general"
        assert "order" in ws_t2["suspended_domains"]
        assert ws_t2["order_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert ws_t2["order_state"]["order_id"] == "7007"

        # ── Turn 3: User returns to Order without providing order number in message ──
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({"id": 7007, "status": "delivered", "tracking_number": "TRK-7007"})

        state_t3 = {
            "session_id": "sess-inter-1",
            "customer_id": 70,
            "message": "Has the shipment arrived yet?",  # Note: NO order ID in text!
            "conversation_history": state_t2["conversation_history"] + [
                {"role": "customer", "content": state_t2["message"]},
                {"role": "agent", "content": res_t2["response"]},
            ],
            "workflow_state": ws_t2,
        }
        res_t3 = await domain_support_graph.ainvoke(state_t3)
        ws_t3 = res_t3["workflow_state"]

        assert res_t3["semantic_domain"] == "order"
        assert res_t3["supervisor_action"] == SupervisorAction.RESUME.value
        assert res_t3["resumed_domain"] == "order"
        assert ws_t3["active_domain"] == "order"
        assert "order" not in ws_t3["suspended_domains"]
        assert ws_t3["order_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        # MUST have resumed from persisted order_state without asking user for order number!
        assert "delivered" in res_t3["response"]
        mock_track.assert_called_with(7007, 70)

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_repeated_switches_do_not_duplicate_approvals(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        """Repeatedly switching away and back to cancellation reuses the existing PendingApproval."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # Step 1: Cancellation proposed
        mock_classify.return_value = _route("order", "cancel_order")
        state_t1 = {
            "session_id": "sess-dup-appr",
            "customer_id": 88,
            "message": "Cancel order #8801",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-dup-appr", "customer_id": 88},
        }
        res_t1 = await domain_support_graph.ainvoke(state_t1)
        p1 = res_t1["pending_approval"]
        assert p1 is not None
        orig_approval_id = p1["approval_id"]
        orig_op_key = p1["operation_key"]

        # Step 2: Product inquiry
        mock_classify.return_value = _route("product", "product_inquiry")
        mock_product.return_value = {"response": "The item is black.", "escalated": False}
        state_t2 = {
            "session_id": "sess-dup-appr",
            "customer_id": 88,
            "message": "What color is it?",
            "conversation_history": [],
            "pending_approval": p1,
            "workflow_state": res_t1["workflow_state"],
        }
        res_t2 = await domain_support_graph.ainvoke(state_t2)

        # Step 3: Return to cancellation request
        mock_classify.return_value = _route("order", "cancel_order")
        state_t3 = {
            "session_id": "sess-dup-appr",
            "customer_id": 88,
            "message": "Can you cancel order #8801?",
            "conversation_history": [],
            "pending_approval": res_t2["pending_approval"],
            "workflow_state": res_t2["workflow_state"],
        }
        res_t3 = await domain_support_graph.ainvoke(state_t3)
        p3 = res_t3["pending_approval"]

        # Invariant: Approval ID and Operation Key MUST be reused, not regenerated!
        assert p3["approval_id"] == orig_approval_id
        assert p3["operation_key"] == orig_op_key

    def test_multi_level_suspension_retains_all_states(self):
        """Three domains sequentially suspended: Product -> Order -> Payment -> General -> Resume Product."""
        ws = WorkflowState(
            session_id="sess-multi-sus",
            customer_id=99,
            active_domain=OrchestrationDomain.PRODUCT.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_state=ProductState(domain_status=DomainWorkflowStatus.IN_PROGRESS, product_name="Mouse"),
        )

        # Switch 1: Product -> Order
        d1 = Supervisor.decide("order", "tracking", ws, "Where is my order?")
        ws = Supervisor.apply_decision(ws, d1)
        assert ws.active_domain == "order"
        assert ws.suspended_domains == [OrchestrationDomain.PRODUCT]

        # Switch 2: Order -> Payment
        d2 = Supervisor.decide("payment", "refund", ws, "Refund my order")
        ws = Supervisor.apply_decision(ws, d2)
        assert ws.active_domain == "payment"
        assert ws.suspended_domains == [OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER]

        # Switch 3: Payment -> General (non-suspendable target)
        d3 = Supervisor.decide("general", "faq", ws, "Store hours")
        ws = Supervisor.apply_decision(ws, d3)
        assert ws.active_domain == "general"
        assert ws.suspended_domains == [OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER, OrchestrationDomain.PAYMENT]

        # Resume Product
        d4 = Supervisor.decide("product", "product_inquiry", ws, "Tell me about the mouse")
        assert d4.action == SupervisorAction.RESUME
        assert d4.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT
        ws = Supervisor.apply_decision(ws, d4)

        assert ws.active_domain == "product"
        assert ws.resumed_domain == OrchestrationDomain.PRODUCT
        assert ws.product_state.product_name == "Mouse"
        assert ws.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
        # Order and Payment remain safely suspended
        assert ws.suspended_domains == [OrchestrationDomain.ORDER, OrchestrationDomain.PAYMENT]


# ──────────────────────────────────────────────────────────────
#  3. Pending Approval Safety Across Domain Switches (Phase D)
# ──────────────────────────────────────────────────────────────

class TestPendingApprovalCrossDomainSafety:
    """Security-critical verification of approval isolation, ambiguity guard, and execution."""

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    async def test_ambiguous_affirmation_in_different_domain_triggers_safe_clarification(
        self, mock_emit, monkeypatch
    ):
        """Customer says bare 'yes' while active domain is Product and pending refund is in Payment."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-ambig-{uuid.uuid4().hex[:8]}"
        cust_id = 111

        approval = create_pending_approval(
            action="process_refund",
            params={"order_id": 9001},
            customer_id=cust_id,
            session_id=session_id,
            message="Shall I refund order #9001?",
        )

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [
                {"role": "agent", "content": "The Pro Mouse has a rechargeable battery. Would you like more details?"}
            ],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {
                "session_id": session_id,
                "customer_id": cust_id,
                "active_domain": "product",  # Active domain is product!
                "suspended_domains": ["payment"],
                "product_state": {"domain_status": "in_progress", "product_name": "Pro Mouse"},
                "payment_state": {"domain_status": "suspended"},
            },
        }

        await session_store.save_session(session_id, initial_session)

        # Mock WebSocket client
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-ambig-1"}),  # Bare affirmation!
            asyncio.CancelledError(),  # Disconnect after 1 turn
        ]

        # Intercept messages sent to the client
        sent_messages = []
        async def mock_send(data, sid):
            sent_messages.append(data)

        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=mock_send), \
             patch("app.websocket.chat_handler.execute_approved_action") as mock_exec, \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

            # INVARIANT: Must NOT execute the refund!
            mock_exec.assert_not_called()

            # INVARIANT: Must ask for safe clarification
            agent_msgs = [m for m in sent_messages if m.get("type") == "agent_response"]
            assert len(agent_msgs) == 1
            assert "pending refund request for order #9001" in agent_msgs[0]["message"]
            assert "previous discussion" in agent_msgs[0]["message"]

            # INVARIANT: Approval must remain pending in persistence
            saved_session = await session_store.get_session(session_id)
            assert saved_session["pending_approval"] is not None
            assert saved_session["pending_approval"]["approval_id"] == approval.approval_id
            assert saved_session["pending_approval"]["status"] == ApprovalStatus.PENDING.value

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    @patch("app.websocket.chat_handler.execute_approved_action")
    async def test_explicit_confirmation_resumes_domain_and_executes(
        self, mock_exec, mock_emit, monkeypatch
    ):
        """Customer explicitly confirms 'Yes, refund order 9001' while active domain was Product."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-explicit-{uuid.uuid4().hex[:8]}"
        cust_id = 222

        approval = create_pending_approval(
            action="process_refund",
            params={"order_id": 9001},
            customer_id=cust_id,
            session_id=session_id,
            message="Shall I refund order #9001?",
        )

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {
                "session_id": session_id,
                "customer_id": cust_id,
                "active_domain": "product",
                "suspended_domains": ["payment"],
                "product_state": {"domain_status": "in_progress"},
                "payment_state": {"domain_status": "suspended"},
            },
        }

        await session_store.save_session(session_id, initial_session)

        mock_exec.return_value = MagicMock(
            success=True,
            action="process_refund",
            response_text="Successfully processed refund for order #9001.",
            is_uncertain=False,
        )

        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "Yes, please refund order 9001", "client_request_id": "req-expl-1"}),
            asyncio.CancelledError(),
        ]

        sent_messages = []
        async def mock_send(data, sid):
            sent_messages.append(data)

        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=mock_send), \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

            # Invariant: Executed once
            mock_exec.assert_called_once()

            # Invariant: Session cleaned up and domain resumed/completed
            saved_session = await session_store.get_session(session_id)
            assert saved_session.get("pending_approval") is None
            assert saved_session["workflow_state"]["active_domain"] == "payment"
            assert saved_session["workflow_state"]["payment_state"]["domain_status"] == "completed"

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    async def test_cross_customer_approval_isolation_rejected(
        self, mock_emit, monkeypatch
    ):
        """Customer B cannot authorize Customer A's pending approval."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-iso-{uuid.uuid4().hex[:8]}"
        cust_a = 100
        cust_b = 200

        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 5005},
            customer_id=cust_a,  # Belongs to Customer A
            session_id=session_id,
            message="Cancel order 5005?",
        )

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_b,  # Session accessed by Customer B
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {"session_id": session_id, "customer_id": cust_b, "active_domain": "order"},
        }
        await session_store.save_session(session_id, initial_session)

        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-iso-1"}),
            asyncio.CancelledError(),
        ]

        sent_messages = []
        async def mock_send(data, sid):
            sent_messages.append(data)

        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=mock_send), \
             patch("app.websocket.chat_handler.execute_approved_action") as mock_exec, \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=cust_b)
            except asyncio.CancelledError:
                pass

            mock_exec.assert_not_called()
            agent_msgs = [m for m in sent_messages if m.get("type") == "agent_response"]
            assert "Identity mismatch" in agent_msgs[0]["message"]


# ──────────────────────────────────────────────────────────────
#  4. Multi-Domain Ambiguity & Fallback Isolation
# ──────────────────────────────────────────────────────────────

class TestMultiDomainAmbiguityAndFallback:
    """Verifies that ambiguous multi-domain requests trigger safe clarification without legacy fallback."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_unknown_or_ambiguous_domain_requests_clarification_safely(
        self, mock_classify, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        # LLM returns domain="unknown"
        mock_classify.return_value = _route("unknown", "unknown")

        state = {
            "session_id": "sess-amb-1",
            "customer_id": 50,
            "message": "Can you please cancel my order 1001 and also refund payment 2002?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-amb-1", "customer_id": 50},
        }

        res = await domain_support_graph.ainvoke(state)

        # Must route to safe_clarification, NOT legacy_fallback
        assert res["supervisor_action"] == SupervisorAction.REQUEST_CLARIFICATION.value
        assert "clarify" in res["response"].lower() or "details" in res["response"].lower()
        assert res["escalated"] is False
        assert res.get("pending_approval") is None


# ──────────────────────────────────────────────────────────────
#  5. Concurrency, Ticket Context & Metadata Resilience
# ──────────────────────────────────────────────────────────────

class TestConcurrencyAndMetadataResilience:
    """Verifies concurrent execution protection, unresolved attempts, and metadata resilience."""

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    @patch("app.websocket.chat_handler.execute_approved_action")
    async def test_concurrent_confirmation_after_resumption_executes_only_once(
        self, mock_exec, mock_emit, monkeypatch
    ):
        """Simultaneous confirmations on a resumed pending approval result in exactly one execution."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-conc-{uuid.uuid4().hex[:8]}"
        cust_id = 777

        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 7777},
            customer_id=cust_id,
            session_id=session_id,
            message="Cancel order 7777?",
        )

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {
                "session_id": session_id,
                "customer_id": cust_id,
                "active_domain": "order",
            },
        }
        await session_store.save_session(session_id, initial_session)

        async def delayed_exec(*args, **kwargs):
            await asyncio.sleep(0.05)
            return MagicMock(
                success=True,
                action="cancel_order",
                response_text="Order #7777 has been successfully cancelled.",
                is_uncertain=False,
            )
        mock_exec.side_effect = delayed_exec

        # Mock two clients connecting and sending confirmation concurrently
        sent_messages = []
        async def mock_send(data, sid):
            sent_messages.append(data)

        mock_ws_1 = AsyncMock()
        mock_ws_1.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-conc-1"}),
            asyncio.CancelledError(),
        ]

        mock_ws_2 = AsyncMock()
        mock_ws_2.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-conc-2"}),
            asyncio.CancelledError(),
        ]

        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=mock_send), \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            task1 = asyncio.create_task(handle_customer_ws(mock_ws_1, session_id=session_id, authenticated_customer_id=cust_id))
            task2 = asyncio.create_task(handle_customer_ws(mock_ws_2, session_id=session_id, authenticated_customer_id=cust_id))
            await asyncio.gather(task1, task2)

        # Invariant: execute_approved_action MUST only be executed once!
        assert mock_exec.call_count == 1

        # One connection received success
        assert any("successfully cancelled" in m.get("message", "") for m in sent_messages)

        # The concurrent connection received already_claimed or already completed message
        assert any("already" in m.get("message", "").lower() for m in sent_messages)

    def test_stale_or_missing_suspended_domains_metadata_handled_gracefully(self):
        """WorkflowState correctly handles legacy dictionaries missing suspended_domains or resumed_domain."""
        legacy_dict = {
            "session_id": "sess-legacy-1",
            "customer_id": 55,
            "active_domain": "product",
            "product_id": 12,
            "product_name": "Keyboard",
            # suspended_domains and resumed_domain are completely missing
        }
        ws = WorkflowState.from_legacy(legacy_dict)
        assert ws.active_domain == "product"
        assert ws.suspended_domains == []
        assert ws.resumed_domain is None
        assert ws.product_state.product_name == "Keyboard"

        # Supervisor can seamlessly decide on this state
        d = Supervisor.decide("order", "tracking", ws, "Where is my order?")
        assert d.action == SupervisorAction.SUSPEND_AND_SWITCH
        ws_switched = Supervisor.apply_decision(ws, d)
        assert ws_switched.active_domain == "order"
        assert ws_switched.suspended_domains == [OrchestrationDomain.PRODUCT]
        assert ws_switched.resumed_domain is None

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_domain_switch_preserves_ticket_context_and_state(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        """Ticket IDs and unresolved attempts remain preserved across multi-domain turns."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        initial_ws = WorkflowState(
            session_id="sess-ticket-iso",
            customer_id=66,
            active_domain=OrchestrationDomain.ORDER.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            unresolved_attempts=2,
            order_state=OrderState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                order_id="6601",
                active_ticket_id=8812,  # Active ticket in Order domain
            ),
        )

        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {
            "response": "The item is covered under warranty.",
            "escalated": False,
        }

        state = {
            "session_id": "sess-ticket-iso",
            "customer_id": 66,
            "message": "Is this mouse covered by warranty?",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        # Order state is suspended, but its active_ticket_id remains 8812!
        assert ws["active_domain"] == "product"
        assert "order" in ws["suspended_domains"]
        assert ws["order_state"]["active_ticket_id"] == 8812
        assert ws["order_state"]["order_id"] == "6601"


# ──────────────────────────────────────────────────────────────
#  6. Exact Matrix Scenarios & Cryptographic Approval Hardening (Prompt 8 Phase D)
# ──────────────────────────────────────────────────────────────

class TestExactMatrixScenariosAndApprovalHardening:
    """Rigorous tests covering exact matrix scenarios 2, 8, 9, cryptographic signatures, and invariants."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_scenario_2_order_to_product_warranty_lookup(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        """Scenario 2: Order -> Product: 'What is the warranty on my headphones?' preserves order_state and executes read-only warranty check."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {
            "response": "The Pro Headphones carry a 2-year warranty.",
            "escalated": False,
        }

        initial_ws = WorkflowState(
            session_id="sess-scen-2",
            customer_id=101,
            active_domain=OrchestrationDomain.ORDER.value,
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            order_state=OrderState(
                domain_status=DomainWorkflowStatus.IN_PROGRESS,
                order_id="1001",
            ),
        )

        state = {
            "session_id": "sess-scen-2",
            "customer_id": 101,
            "message": "What is the warranty on my headphones?",
            "conversation_history": [],
            "workflow_state": initial_ws.model_dump(mode="json"),
        }

        res = await domain_support_graph.ainvoke(state)
        ws = res["workflow_state"]

        assert res["semantic_domain"] == "product"
        assert res["supervisor_action"] == SupervisorAction.SUSPEND_AND_SWITCH.value
        assert ws["active_domain"] == OrchestrationDomain.PRODUCT.value
        assert OrchestrationDomain.ORDER.value in ws["suspended_domains"]
        assert ws["order_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert ws["order_state"]["order_id"] == "1001"
        assert ws["product_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        assert res.get("pending_approval") is None
        assert not res.get("escalated")

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    @patch("app.websocket.chat_handler.execute_approved_action")
    async def test_scenario_8_pending_refund_interrupted_by_general_and_confirmed(
        self, mock_exec, mock_emit, monkeypatch
    ):
        """Scenario 8: Pending refund #4004 -> General query -> Explicit confirmation resumes Payment and executes once."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-scen8-{uuid.uuid4().hex[:8]}"
        cust_id = 404

        approval = create_pending_approval(
            action="process_refund",
            params={"order_id": 4004},
            customer_id=cust_id,
            session_id=session_id,
            message="Shall I process the refund for order #4004?",
        )
        assert approval.signature is not None

        # Turn 1: Interrupted by general informational turn
        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {
                "session_id": session_id,
                "customer_id": cust_id,
                "active_domain": "general",
                "suspended_domains": ["payment"],
                "payment_state": {"domain_status": "suspended"},
            },
        }
        await session_store.save_session(session_id, initial_session)

        mock_exec.return_value = MagicMock(
            success=True,
            action="process_refund",
            response_text="Refund initiated for order #4004.",
            is_uncertain=False,
        )

        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "Yes, go ahead and refund order 4004", "client_request_id": "req-scen8-1"}),
            asyncio.CancelledError(),
        ]

        sent_messages = []
        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=lambda d, s: sent_messages.append(d)), \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

        mock_exec.assert_called_once()
        saved = await session_store.get_session(session_id)
        assert saved.get("pending_approval") is None
        assert saved["workflow_state"]["active_domain"] == "payment"
        assert saved["workflow_state"]["payment_state"]["domain_status"] == "completed"

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    @patch("app.websocket.chat_handler.execute_approved_action")
    async def test_scenario_9_pending_cancellation_ambiguous_affirmation_then_explicit(
        self, mock_exec, mock_emit, monkeypatch
    ):
        """Scenario 9: Pending cancellation #5005 -> Product active -> Bare 'yes' asks clarification; Explicit confirms and executes."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-scen9-{uuid.uuid4().hex[:8]}"
        cust_id = 505

        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 5005},
            customer_id=cust_id,
            session_id=session_id,
            message="Would you like to cancel order #5005?",
        )

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": approval.model_dump(mode="json"),
            "workflow_state": {
                "session_id": session_id,
                "customer_id": cust_id,
                "active_domain": "product",
                "suspended_domains": ["order"],
                "product_state": {"domain_status": "in_progress", "product_name": "Keyboard"},
                "order_state": {"domain_status": "suspended", "order_id": "5005"},
            },
        }
        await session_store.save_session(session_id, initial_session)

        # Step 1: Bare affirmation "yes"
        mock_ws_1 = AsyncMock()
        mock_ws_1.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-scen9-1"}),
            asyncio.CancelledError(),
        ]

        sent_messages_1 = []
        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=lambda d, s: sent_messages_1.append(d)), \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws_1, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

        mock_exec.assert_not_called()
        agent_msgs_1 = [m for m in sent_messages_1 if m.get("type") == "agent_response"]
        assert len(agent_msgs_1) == 1
        assert "cancellation request for order #5005" in agent_msgs_1[0]["message"]

        # Step 2: Explicit confirmation
        mock_exec.return_value = MagicMock(
            success=True,
            action="cancel_order",
            response_text="Order #5005 has been successfully cancelled.",
            is_uncertain=False,
        )

        mock_ws_2 = AsyncMock()
        mock_ws_2.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "Please cancel order 5005", "client_request_id": "req-scen9-2"}),
            asyncio.CancelledError(),
        ]

        sent_messages_2 = []
        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=lambda d, s: sent_messages_2.append(d)), \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws_2, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

        mock_exec.assert_called_once()
        saved = await session_store.get_session(session_id)
        assert saved.get("pending_approval") is None
        assert saved["workflow_state"]["active_domain"] == "order"
        assert saved["workflow_state"]["order_state"]["domain_status"] == "completed"

    def test_cryptographic_hmac_signature_tamper_detection(self):
        """HMAC-SHA256 signature detects tampering with any bound field."""
        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 9999},
            customer_id=123,
            session_id="sess-crypto-1",
            message="Cancel order 9999?",
        )
        assert approval.signature is not None
        assert verify_approval_signature(approval) is True

        # 1. Tamper order_id in params
        tampered_params = approval.model_copy(deep=True)
        tampered_params.params["order_id"] = 8888
        assert verify_approval_signature(tampered_params) is False
        valid, err = validate_approval(tampered_params, authenticated_customer_id=123, session_id="sess-crypto-1")
        assert valid is None
        assert "signature verification failed" in err.lower()

        # 2. Tamper customer_id
        tampered_cust = approval.model_copy(deep=True)
        tampered_cust.customer_id = 999
        assert verify_approval_signature(tampered_cust) is False

        # 3. Tamper operation_key
        tampered_op = approval.model_copy(deep=True)
        tampered_op.operation_key = str(uuid.uuid4())
        assert verify_approval_signature(tampered_op) is False

        # 4. Tamper action
        tampered_action = approval.model_copy(deep=True)
        tampered_action.action = "process_refund"
        assert verify_approval_signature(tampered_action) is False

        # 5. Tamper expires_at
        tampered_exp = approval.model_copy(deep=True)
        tampered_exp.expires_at = "2099-01-01T00:00:00+00:00"
        assert verify_approval_signature(tampered_exp) is False

    def test_general_and_escalation_exclusion_from_suspended_domains(self):
        """GENERAL and ESCALATION are strictly forbidden in suspended_domains."""
        # 1. GENERAL cannot be suspended
        with pytest.raises(ValueError, match="Domain 'general' cannot be stored in suspended_domains"):
            WorkflowState(
                session_id="sess-inv-gen",
                customer_id=1,
                active_domain="product",
                suspended_domains=[OrchestrationDomain.GENERAL],
                product_state=ProductState(domain_status=DomainWorkflowStatus.IN_PROGRESS),
            )

        # 2. ESCALATION cannot be suspended
        with pytest.raises(ValueError, match="Domain 'escalation' cannot be stored in suspended_domains"):
            WorkflowState(
                session_id="sess-inv-esc",
                customer_id=1,
                active_domain="order",
                suspended_domains=[OrchestrationDomain.ESCALATION],
                order_state=OrderState(domain_status=DomainWorkflowStatus.IN_PROGRESS),
            )

    @pytest.mark.asyncio
    @patch("app.websocket.chat_handler.emit_workflow_turn")
    async def test_tampered_approval_signature_rejected_in_websocket(self, mock_emit, monkeypatch):
        """WebSocket handler blocks tampered approval signatures with clear security telemetry."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        session_id = f"sess-tamp-{uuid.uuid4().hex[:8]}"
        cust_id = 333

        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 3333},
            customer_id=cust_id,
            session_id=session_id,
            message="Cancel order 3333?",
        )
        dumped = approval.model_dump(mode="json")
        # Tamper signature
        dumped["signature"] = "deadbeefcafebabe000000000000000000000000000000000000000000000000"

        initial_session = {
            "session_id": session_id,
            "customer_id": cust_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "pending_approval": dumped,
            "workflow_state": {"session_id": session_id, "customer_id": cust_id, "active_domain": "order"},
        }
        await session_store.save_session(session_id, initial_session)

        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            json.dumps({"type": "message", "message": "yes", "client_request_id": "req-tamp-1"}),
            asyncio.CancelledError(),
        ]

        sent_messages = []
        with patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=lambda d, s: sent_messages.append(d)), \
             patch("app.websocket.chat_handler.execute_approved_action") as mock_exec, \
             patch("app.persistence.escalation.ensure_durable_escalation_state", return_value=MagicMock(value="NONE")):

            try:
                await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=cust_id)
            except asyncio.CancelledError:
                pass

            mock_exec.assert_not_called()
            agent_msgs = [m for m in sent_messages if m.get("type") == "agent_response"]
            assert len(agent_msgs) == 1
            assert "signature mismatch or tampering" in agent_msgs[0]["message"].lower()
