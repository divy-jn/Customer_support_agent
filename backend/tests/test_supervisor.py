import pytest
import inspect
from app.models import (
    WorkflowState, GlobalWorkflowStatus, DomainWorkflowStatus,
    OrchestrationDomain, ProductState, OrderState, PaymentState,
)
from app.agents.supervisor import (
    Supervisor, SupervisorAction, SupervisorDecision, TransitionMetadata,
    InvalidSupervisorDecisionError,
)


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _base(
    active: str | None = None,
    global_status: GlobalWorkflowStatus = GlobalWorkflowStatus.IDLE,
    product_status: DomainWorkflowStatus | None = None,
    order_status: DomainWorkflowStatus | None = None,
    payment_status: DomainWorkflowStatus | None = None,
    suspended: list[OrchestrationDomain] | None = None,
    product_facts: dict | None = None,
    order_facts: dict | None = None,
    payment_facts: dict | None = None,
) -> WorkflowState:
    """Build a WorkflowState directly from typed F.2 fields — no legacy path."""
    product_state = None
    order_state = None
    payment_state = None

    if product_status is not None:
        kw = {"domain_status": product_status}
        if product_facts:
            kw.update(product_facts)
        product_state = ProductState(**kw)

    if order_status is not None:
        kw = {"domain_status": order_status}
        if order_facts:
            kw.update(order_facts)
        order_state = OrderState(**kw)

    if payment_status is not None:
        kw = {"domain_status": payment_status}
        if payment_facts:
            kw.update(payment_facts)
        payment_state = PaymentState(**kw)

    return WorkflowState(
        session_id="test_session",
        customer_id=999,
        global_status=global_status,
        active_domain=active,
        suspended_domains=suspended or [],
        product_state=product_state,
        order_state=order_state,
        payment_state=payment_state,
    )


# ──────────────────────────────────────────────
#  1. product → order → product RESUME
# ──────────────────────────────────────────────

def test_product_order_product_resume():
    """Scenario: PRODUCT active → switch to ORDER → return to PRODUCT must RESUME."""
    # Step 1: PRODUCT is active and IN_PROGRESS
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    # Step 2: User switches to ORDER
    d1 = Supervisor.decide("order", "track", state, "where is my order")
    assert d1.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert d1.target_domain == OrchestrationDomain.ORDER
    assert d1.transition_metadata.suspended_domain == OrchestrationDomain.PRODUCT

    state = Supervisor.apply_decision(state, d1)
    assert state.active_domain == "order"
    assert state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert OrchestrationDomain.PRODUCT in state.suspended_domains
    assert state.order_state.domain_status == DomainWorkflowStatus.IN_PROGRESS

    # Step 3: User asks about PRODUCT again → MUST be RESUME
    d2 = Supervisor.decide("product", "support", state, "back to my broken phone")
    assert d2.action == SupervisorAction.RESUME
    assert d2.target_domain == OrchestrationDomain.PRODUCT
    assert d2.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT

    state = Supervisor.apply_decision(state, d2)
    assert state.active_domain == "product"
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.PRODUCT not in state.suspended_domains


# ──────────────────────────────────────────────
#  2. product → general → product RESUME
# ──────────────────────────────────────────────

def test_product_general_product_resume():
    """PRODUCT → GENERAL (non-conversational) → PRODUCT must RESUME, not SUSPEND_AND_SWITCH."""
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    # Switch to GENERAL
    d1 = Supervisor.decide("general", "policy_inquiry", state, "what is the return policy")
    assert d1.action == SupervisorAction.SUSPEND_AND_SWITCH
    state = Supervisor.apply_decision(state, d1)
    assert state.active_domain == "general"
    assert OrchestrationDomain.PRODUCT in state.suspended_domains

    # Return to PRODUCT
    d2 = Supervisor.decide("product", "support", state, "back to my phone issue")
    assert d2.action == SupervisorAction.RESUME
    assert d2.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT

    state = Supervisor.apply_decision(state, d2)
    assert state.active_domain == "product"
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.PRODUCT not in state.suspended_domains


# ──────────────────────────────────────────────
#  3. order → product → order RESUME
# ──────────────────────────────────────────────

def test_order_product_order_resume():
    state = _base(
        active="order",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        order_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d1 = Supervisor.decide("product", "inquiry", state, "tell me about my phone")
    assert d1.action == SupervisorAction.SUSPEND_AND_SWITCH
    state = Supervisor.apply_decision(state, d1)
    assert OrchestrationDomain.ORDER in state.suspended_domains

    d2 = Supervisor.decide("order", "track", state, "back to order status")
    assert d2.action == SupervisorAction.RESUME
    assert d2.transition_metadata.resumed_domain == OrchestrationDomain.ORDER

    state = Supervisor.apply_decision(state, d2)
    assert state.active_domain == "order"
    assert state.order_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.ORDER not in state.suspended_domains


# ──────────────────────────────────────────────
#  4. payment → order → payment RESUME
# ──────────────────────────────────────────────

def test_payment_order_payment_resume():
    state = _base(
        active="payment",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        payment_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d1 = Supervisor.decide("order", "track", state, "where is my order")
    assert d1.action == SupervisorAction.SUSPEND_AND_SWITCH
    state = Supervisor.apply_decision(state, d1)
    assert OrchestrationDomain.PAYMENT in state.suspended_domains

    d2 = Supervisor.decide("payment", "billing", state, "back to my payment issue")
    assert d2.action == SupervisorAction.RESUME
    assert d2.transition_metadata.resumed_domain == OrchestrationDomain.PAYMENT

    state = Supervisor.apply_decision(state, d2)
    assert state.active_domain == "payment"
    assert state.payment_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.PAYMENT not in state.suspended_domains


# ──────────────────────────────────────────────
#  5. multiple suspended domains — select correct one
# ──────────────────────────────────────────────

def test_multiple_suspended_domains_correct_selection():
    """Both PRODUCT and ORDER suspended; requesting PRODUCT resumes only PRODUCT."""
    state = _base(
        active="payment",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.SUSPENDED,
        order_status=DomainWorkflowStatus.SUSPENDED,
        payment_status=DomainWorkflowStatus.IN_PROGRESS,
        suspended=[OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER],
        product_facts={"product_name": "SuperPhone X", "active_ticket_id": 42},
        order_facts={"order_id": "789"},
    )

    d = Supervisor.decide("product", "support", state, "my phone again")
    assert d.action == SupervisorAction.RESUME
    assert d.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT

    new_state = Supervisor.apply_decision(state, d)
    assert new_state.active_domain == "product"
    assert OrchestrationDomain.PRODUCT not in new_state.suspended_domains
    # ORDER must remain suspended and untouched
    assert OrchestrationDomain.ORDER in new_state.suspended_domains
    assert new_state.order_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert new_state.order_state.order_id == "789"


# ──────────────────────────────────────────────
#  6. resumed domain removed from suspended_domains
# ──────────────────────────────────────────────

def test_resumed_domain_removed_from_suspended():
    state = _base(
        active="order",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.SUSPENDED,
        order_status=DomainWorkflowStatus.IN_PROGRESS,
        suspended=[OrchestrationDomain.PRODUCT],
    )
    d = Supervisor.decide("product", "support", state, "phone issue")
    new_state = Supervisor.apply_decision(state, d)
    assert OrchestrationDomain.PRODUCT not in new_state.suspended_domains
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS


# ──────────────────────────────────────────────
#  7. unrelated suspended domains remain untouched
# ──────────────────────────────────────────────

def test_unrelated_suspended_domains_untouched():
    state = _base(
        active="payment",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.SUSPENDED,
        order_status=DomainWorkflowStatus.SUSPENDED,
        payment_status=DomainWorkflowStatus.IN_PROGRESS,
        suspended=[OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER],
        product_facts={"product_name": "Widget", "active_ticket_id": 10},
    )
    d = Supervisor.decide("order", "track", state, "check my order")
    new_state = Supervisor.apply_decision(state, d)
    # ORDER is resumed
    assert OrchestrationDomain.ORDER not in new_state.suspended_domains
    # PRODUCT stays suspended
    assert OrchestrationDomain.PRODUCT in new_state.suspended_domains
    assert new_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert new_state.product_state.product_name == "Widget"
    assert new_state.product_state.active_ticket_id == 10


# ──────────────────────────────────────────────
#  8. active domain never remains in suspended_domains
# ──────────────────────────────────────────────

def test_active_domain_never_in_suspended_domains():
    """After any apply_decision, the new active_domain must not appear in suspended_domains."""
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    # Switch product -> order
    d1 = Supervisor.decide("order", "track", state, "order please")
    s1 = Supervisor.apply_decision(state, d1)
    assert OrchestrationDomain(s1.active_domain) not in s1.suspended_domains

    # Resume product
    d2 = Supervisor.decide("product", "support", s1, "back to phone")
    s2 = Supervisor.apply_decision(s1, d2)
    assert OrchestrationDomain(s2.active_domain) not in s2.suspended_domains

    # Start new from idle
    idle = _base()
    d3 = Supervisor.decide("product", "support", idle, "help")
    s3 = Supervisor.apply_decision(idle, d3)
    assert OrchestrationDomain(s3.active_domain) not in s3.suspended_domains


# ──────────────────────────────────────────────
#  9. invalid manually-created SupervisorDecision rejected
# ──────────────────────────────────────────────

class TestInvalidDecisionRejected:

    def test_resume_without_resumed_domain(self):
        state = _base(
            active="order",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.SUSPENDED,
            order_status=DomainWorkflowStatus.IN_PROGRESS,
            suspended=[OrchestrationDomain.PRODUCT],
        )
        bad = SupervisorDecision(
            action=SupervisorAction.RESUME,
            target_domain=OrchestrationDomain.PRODUCT,
            transition_reason="manual",
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="resumed_domain"):
            Supervisor.apply_decision(state, bad)

    def test_resume_not_in_suspended(self):
        state = _base(
            active="order",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            order_status=DomainWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.RESUME,
            target_domain=OrchestrationDomain.PRODUCT,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain.PRODUCT),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="not in suspended"):
            Supervisor.apply_decision(state, bad)

    def test_resume_target_mismatch(self):
        state = _base(
            active="order",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.SUSPENDED,
            order_status=DomainWorkflowStatus.IN_PROGRESS,
            suspended=[OrchestrationDomain.PRODUCT],
        )
        bad = SupervisorDecision(
            action=SupervisorAction.RESUME,
            target_domain=OrchestrationDomain.ORDER,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain.PRODUCT),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="does not match"):
            Supervisor.apply_decision(state, bad)

    def test_suspend_without_suspended_domain(self):
        state = _base(
            active="product",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.SUSPEND_AND_SWITCH,
            target_domain=OrchestrationDomain.ORDER,
            transition_reason="manual",
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="suspended_domain"):
            Supervisor.apply_decision(state, bad)

    def test_suspend_wrong_active_domain(self):
        state = _base(
            active="product",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.IN_PROGRESS,
            order_status=DomainWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.SUSPEND_AND_SWITCH,
            target_domain=OrchestrationDomain.PAYMENT,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain.ORDER),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="does not match current active"):
            Supervisor.apply_decision(state, bad)

    def test_suspend_non_suspendable_domain(self):
        """GENERAL is not a suspendable workflow domain."""
        state = _base(
            active="general",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.SUSPEND_AND_SWITCH,
            target_domain=OrchestrationDomain.PRODUCT,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain.GENERAL),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="not a suspendable"):
            Supervisor.apply_decision(state, bad)

    def test_continue_without_active_domain(self):
        state = _base()
        bad = SupervisorDecision(
            action=SupervisorAction.CONTINUE,
            target_domain=OrchestrationDomain.PRODUCT,
            transition_reason="manual",
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="active workflow"):
            Supervisor.apply_decision(state, bad)

    def test_metadata_mismatch_resumed_on_non_resume(self):
        state = _base(
            active="product",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.START_NEW,
            target_domain=OrchestrationDomain.ORDER,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain.PRODUCT),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="resumed_domain is set"):
            Supervisor.apply_decision(state, bad)

    def test_metadata_mismatch_suspended_on_non_switch(self):
        state = _base(
            active="product",
            global_status=GlobalWorkflowStatus.IN_PROGRESS,
            product_status=DomainWorkflowStatus.IN_PROGRESS,
        )
        bad = SupervisorDecision(
            action=SupervisorAction.START_NEW,
            target_domain=OrchestrationDomain.ORDER,
            transition_reason="manual",
            transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain.PRODUCT),
        )
        with pytest.raises(InvalidSupervisorDecisionError, match="suspended_domain is set"):
            Supervisor.apply_decision(state, bad)


# ──────────────────────────────────────────────
#  10. Supervisor never reads workflow_status
# ──────────────────────────────────────────────

def test_supervisor_never_reads_workflow_status():
    """Supervisor source code must not reference 'workflow_status' anywhere."""
    source = inspect.getsource(Supervisor)
    assert "workflow_status" not in source, (
        "Supervisor source code still references 'workflow_status'"
    )


# ──────────────────────────────────────────────
#  11. Supervisor never writes workflow_status
# ──────────────────────────────────────────────

def test_supervisor_never_writes_workflow_status():
    """apply_decision must not mutate workflow_status on the returned state."""
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    original_ws = state.workflow_status
    d = Supervisor.decide("order", "track", state, "order please")
    new_state = Supervisor.apply_decision(state, d)
    # workflow_status must be unchanged (default or whatever it was)
    assert new_state.workflow_status == original_ws


# ──────────────────────────────────────────────
#  12. contradictory legacy workflow_status cannot override typed state
# ──────────────────────────────────────────────

def test_contradictory_legacy_status_does_not_override_typed():
    """Even if workflow_status says COMPLETED, typed domain_status is authoritative."""
    from app.models import WorkflowStatus
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    # Force contradictory legacy field
    state.workflow_status = WorkflowStatus.COMPLETED

    d = Supervisor.decide("product", "support", state, "still fixing phone")
    # Typed state says IN_PROGRESS → CONTINUE, regardless of legacy
    assert d.action == SupervisorAction.CONTINUE
    assert d.target_domain == OrchestrationDomain.PRODUCT


# ──────────────────────────────────────────────
#  13. original WorkflowState remains unchanged after apply_decision()
# ──────────────────────────────────────────────

def test_original_state_unchanged_after_apply():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
        product_facts={"product_name": "SuperPhone X", "active_ticket_id": 42},
    )
    original_dump = state.model_dump(mode="json")

    d = Supervisor.decide("order", "track", state, "order status")
    _new = Supervisor.apply_decision(state, d)

    assert state.model_dump(mode="json") == original_dump


# ──────────────────────────────────────────────
#  Existing behavior regression tests
# ──────────────────────────────────────────────

def test_product_workflow_product_message():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d = Supervisor.decide("product", "support", state, "my phone is broken")
    assert d.action == SupervisorAction.CONTINUE
    assert d.target_domain == OrchestrationDomain.PRODUCT
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS


def test_product_workflow_general_conversational():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.AWAITING_INPUT,
    )
    d = Supervisor.decide("general", "greeting", state, "hi")
    assert d.action == SupervisorAction.CONTINUE
    assert d.target_domain == OrchestrationDomain.PRODUCT
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.product_state.domain_status == DomainWorkflowStatus.AWAITING_INPUT


def test_product_workflow_general_unrelated():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d = Supervisor.decide("general", "policy_inquiry", state, "return policy")
    assert d.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert d.target_domain == OrchestrationDomain.GENERAL
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.active_domain == OrchestrationDomain.GENERAL.value
    assert new_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert OrchestrationDomain.PRODUCT in new_state.suspended_domains


def test_order_workflow_general_conversational():
    state = _base(
        active="order",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        order_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d = Supervisor.decide("general", "small_talk", state, "how are you")
    assert d.action == SupervisorAction.CONTINUE
    assert d.target_domain == OrchestrationDomain.ORDER


def test_completed_product_general_request():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.COMPLETED,
    )
    d = Supervisor.decide("general", "policy_inquiry", state, "return policy")
    assert d.action == SupervisorAction.START_NEW
    assert d.target_domain == OrchestrationDomain.GENERAL


def test_general_active_workflow_product_switch():
    state = _base(
        active="general",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
    )
    d = Supervisor.decide("product", "support", state, "phone broken")
    # GENERAL is not a suspendable workflow domain — switching away is START_NEW
    assert d.action == SupervisorAction.START_NEW
    assert d.target_domain == OrchestrationDomain.PRODUCT


def test_product_workflow_order_message():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d = Supervisor.decide("order", "track", state, "where is my order")
    assert d.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert d.target_domain == OrchestrationDomain.ORDER
    assert d.transition_metadata.suspended_domain == OrchestrationDomain.PRODUCT
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.active_domain == OrchestrationDomain.ORDER.value
    assert new_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert new_state.order_state.domain_status == DomainWorkflowStatus.IN_PROGRESS


def test_resume_suspended_workflow():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.SUSPENDED,
        suspended=[OrchestrationDomain.PRODUCT],
    )
    d = Supervisor.decide("product", "support", state, "back to my broken screen")
    assert d.action == SupervisorAction.RESUME
    assert d.target_domain == OrchestrationDomain.PRODUCT
    assert d.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.active_domain == "product"
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.PRODUCT not in new_state.suspended_domains


def test_completed_workflow():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.COMPLETED,
    )
    d = Supervisor.decide("product", "support", state, "another issue")
    assert d.action == SupervisorAction.START_NEW
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS


def test_escalated_workflow():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.ESCALATED,
        product_status=DomainWorkflowStatus.ESCALATED,
    )
    d = Supervisor.decide("product", "support", state, "help")
    assert d.action == SupervisorAction.ESCALATE
    new_state = Supervisor.apply_decision(state, d)
    assert new_state.global_status == GlobalWorkflowStatus.ESCALATED
    assert new_state.product_state.domain_status == DomainWorkflowStatus.ESCALATED


def test_idle_workflow():
    state = _base()
    d = Supervisor.decide("product", "support", state, "help")
    assert d.action == SupervisorAction.START_NEW
    assert d.target_domain == OrchestrationDomain.PRODUCT


def test_invalid_semantic_domain():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    for invalid_domain in ["", "banana", "unknown"]:
        d = Supervisor.decide(invalid_domain, "", state, "ummm")
        assert d.action == SupervisorAction.REQUEST_CLARIFICATION
        assert d.target_domain == OrchestrationDomain.PRODUCT


def test_unknown_invalid_global_status():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    state.global_status = "UNKNOWN_WEIRD_STATE"
    d = Supervisor.decide("product", "support", state, "help")
    assert d.action == SupervisorAction.REQUEST_CLARIFICATION


def test_deterministic_repeatability():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
    )
    d1 = Supervisor.decide("order", "track", state, "where is it")
    d2 = Supervisor.decide("order", "track", state, "where is it")
    assert d1.action == d2.action
    assert d1.target_domain == d2.target_domain


def test_mutation_boundaries():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
        product_facts={"product_name": "Test Product", "product_id": 123, "active_ticket_id": 999},
    )
    d = Supervisor.decide("order", "track", state, "where is it")
    new_state = Supervisor.apply_decision(state, d)

    assert state.active_domain == "product"
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS

    assert new_state.active_domain == "order"
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert new_state.product_state.product_id == 123
    assert new_state.product_state.product_name == "Test Product"
    assert new_state.product_state.active_ticket_id == 999
    assert new_state.customer_id == 999


def test_ticket_id_does_not_override_switch():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.IN_PROGRESS,
        product_facts={"active_ticket_id": 999},
    )
    d = Supervisor.decide("order", "track", state, "where is it")
    assert d.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert d.target_domain == OrchestrationDomain.ORDER


def test_approval_not_silently_authorized():
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.AWAITING_INPUT,
    )
    d = Supervisor.decide("product", "confirm", state, "yes do it")
    assert d.action == SupervisorAction.CONTINUE
    assert not hasattr(d, 'proceed_approval')


# ──────────────────────────────────────────────
#  No legacy WorkflowStatus import in supervisor module
# ──────────────────────────────────────────────

def test_supervisor_module_does_not_import_workflow_status():
    """The supervisor module must not import the legacy WorkflowStatus enum."""
    import re
    import app.agents.supervisor as sup_mod
    assert not hasattr(sup_mod, 'WorkflowStatus'), (
        "supervisor module still exposes WorkflowStatus"
    )
    source = inspect.getsource(sup_mod)
    # Match standalone 'WorkflowStatus' but not 'DomainWorkflowStatus' or 'GlobalWorkflowStatus'
    standalone_refs = re.findall(r'(?<!Domain)(?<!Global)\bWorkflowStatus\b', source)
    assert len(standalone_refs) == 0, (
        f"supervisor module source still references legacy WorkflowStatus: {standalone_refs}"
    )


# ──────────────────────────────────────────────
#  START_NEW cleans up stale suspended_domains
# ──────────────────────────────────────────────

def test_start_new_removes_from_suspended_domains():
    """START_NEW for a domain previously in suspended_domains cleans it up."""
    state = _base(
        active="product",
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_status=DomainWorkflowStatus.SUSPENDED,
        order_status=DomainWorkflowStatus.SUSPENDED,
        suspended=[OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER],
    )
    # Active domain is suspended (edge case), target is ORDER → START_NEW
    d = Supervisor.decide("order", "track", state, "where is my order")
    assert d.action == SupervisorAction.START_NEW
    new_state = Supervisor.apply_decision(state, d)
    # ORDER must not be simultaneously active and in suspended_domains
    assert OrchestrationDomain.ORDER not in new_state.suspended_domains
    assert new_state.active_domain == "order"
