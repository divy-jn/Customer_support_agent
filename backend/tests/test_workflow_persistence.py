import pytest
import json
from datetime import datetime, timezone
from app.models import (
    WorkflowState, ProductState, OrderState, PaymentState,
    OrchestrationDomain, DomainWorkflowStatus, ToolResultEnvelope
)

def test_product_state_json_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(
        domain_status=DomainWorkflowStatus.AWAITING_INPUT,
        active_ticket_id=701,
        product_name="Phone"
    )
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.active_ticket_id == 701
    assert restored.product_state.product_name == "Phone"

def test_order_state_json_round_trip():
    state = WorkflowState(session_id="test")
    state.order_state = OrderState(active_ticket_id=702, order_id="123")
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.order_state.active_ticket_id == 702
    assert restored.order_state.order_id == "123"

def test_payment_state_json_round_trip():
    state = WorkflowState(session_id="test")
    state.payment_state = PaymentState(active_ticket_id=703, transaction_id="tx_999")
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.payment_state.active_ticket_id == 703
    assert restored.payment_state.transaction_id == "tx_999"

def test_active_ticket_ids_survive_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.payment_state = PaymentState(active_ticket_id=703)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.active_ticket_id == 701
    assert restored.order_state.active_ticket_id == 702
    assert restored.payment_state.active_ticket_id == 703

def test_suspended_domains_survive_round_trip():
    state = WorkflowState(session_id="test", active_domain=OrchestrationDomain.GENERAL.value)
    state.product_state = ProductState(domain_status=DomainWorkflowStatus.SUSPENDED)
    state.order_state = OrderState(domain_status=DomainWorkflowStatus.SUSPENDED)
    state.suspended_domains = [OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER]
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.suspended_domains == [OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER]

def test_schema_version_survives_round_trip():
    state = WorkflowState(session_id="test", schema_version=1)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.schema_version == 1

def test_state_revision_survives_round_trip():
    state = WorkflowState(session_id="test", state_revision=42)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.state_revision == 42

def test_tool_result_envelope_survives_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(
        last_tool_result=ToolResultEnvelope(
            tool_name="test_tool",
            result={"success": True}
        )
    )
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.last_tool_result.result == {"success": True}

def test_datetime_fields_survive_round_trip():
    now = datetime.now(timezone.utc)
    state = WorkflowState(session_id="test", updated_at=now)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.updated_at == now

def test_enums_survive_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(domain_status=DomainWorkflowStatus.SUSPENDED)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.domain_status == DomainWorkflowStatus.SUSPENDED

def test_customer_id_session_id_survive_round_trip():
    state = WorkflowState(session_id="sess_123", customer_id=888)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.session_id == "sess_123"
    assert restored.customer_id == 888

def test_cross_domain_isolation_survives_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.active_ticket_id == 701
    assert restored.order_state.active_ticket_id == 702


def test_malformed_persisted_state_fails_closed():
    with pytest.raises(ValueError):
        WorkflowState.from_legacy({"session_id": "test", "active_domain": "INVALID_DOMAIN"})

def test_unsupported_schema_version_fails_closed():
    with pytest.raises(ValueError):
        WorkflowState.from_legacy({"session_id": "test", "schema_version": 999})

def test_invalid_state_revision_fails_closed():
    with pytest.raises(ValueError):
        WorkflowState.model_validate({"session_id": "test", "state_revision": -1})

def test_persistence_round_trip_does_not_mutate_authoritative_domain_states():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701, product_name="Phone")
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    # Validate the exact instance values don't magically shift
    assert restored.product_state.active_ticket_id == 701
    assert restored.product_state.product_name == "Phone"
    assert restored.order_state is None

def test_persistence_round_trip_does_not_silently_increment_state_revision():
    state = WorkflowState(session_id="test", state_revision=4)
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.state_revision == 4

def test_product_ticket_does_not_leak_into_order_state_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.ORDER.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.order_state.active_ticket_id == 702
    assert restored.product_state.active_ticket_id == 701
    
def test_order_ticket_does_not_leak_into_product_state_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.active_ticket_id == 701
    assert restored.order_state.active_ticket_id == 702

def test_payment_ticket_does_not_leak_into_product_state_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.payment_state = PaymentState(active_ticket_id=703)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    assert restored.product_state.active_ticket_id == 701
    assert restored.payment_state.active_ticket_id == 703


def test_malformed_persistence_rejects_general_with_domain_states():
    with pytest.raises(ValueError, match=r'Domain general cannot own active typed domain state \(PRODUCT\)'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'general',
            'product_state': {'domain_status': 'in_progress'}
        })

def test_malformed_persistence_rejects_escalation_with_domain_states():
    with pytest.raises(ValueError, match=r'Domain escalation cannot own active typed domain state \(ORDER\)'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'escalation',
            'order_state': {'domain_status': 'in_progress'}
        })

def test_malformed_persistence_rejects_product_with_contradictory_order_facts():
    with pytest.raises(ValueError, match='active_domain=product contradicts order ownership facts'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'product',
            'order_id': 123,
            'product_state': {'domain_status': 'in_progress'}
        })

def test_malformed_persistence_rejects_payment_with_contradictory_product_facts():
    with pytest.raises(ValueError, match='active_domain=payment contradicts product/order ownership facts'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'payment',
            'product_id': 123,
            'payment_state': {'domain_status': 'in_progress'}
        })

def test_malformed_persistence_rejects_conflicting_root_ticket_identity():
    with pytest.raises(ValueError, match='Conflicting legacy root ticket identity with ProductState'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'product',
            'active_ticket_id': 999,
            'product_state': {'domain_status': 'in_progress', 'active_ticket_id': 123}
        })


def test_supervisor_transition_product_to_general_persistence():
    """I. PRODUCT -> GENERAL transition from Supervisor can be persisted/reloaded."""
    from app.agents.supervisor import Supervisor
    from app.models import GlobalWorkflowStatus
    
    # 1. Start with PRODUCT active
    state = WorkflowState(
        session_id="test",
        active_domain=OrchestrationDomain.PRODUCT,
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        product_state=ProductState(domain_status=DomainWorkflowStatus.IN_PROGRESS)
    )
    
    # 2. Supervisor transition to GENERAL
    decision = Supervisor.decide(
        "general",
        "unrelated_query",
        state,
        "how are you?"
    )
    new_state = Supervisor.apply_decision(state, decision)
    
    assert new_state.active_domain == OrchestrationDomain.GENERAL.value
    assert OrchestrationDomain.PRODUCT in new_state.suspended_domains
    
    # 3. Persistence round trip
    serialized = new_state.model_dump_json()
    reloaded = WorkflowState.model_validate_json(serialized)
    
    assert reloaded.active_domain == OrchestrationDomain.GENERAL
    assert OrchestrationDomain.PRODUCT in reloaded.suspended_domains
    assert reloaded.product_state.domain_status == DomainWorkflowStatus.SUSPENDED

def test_supervisor_transition_resume_persistence():
    """J. PRODUCT -> ORDER -> PRODUCT RESUME can be persisted/reloaded."""
    from app.agents.supervisor import Supervisor
    from app.models import GlobalWorkflowStatus
    
    # 1. Start with PRODUCT suspended, ORDER active
    state = WorkflowState(
        session_id="test",
        active_domain=OrchestrationDomain.ORDER,
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        suspended_domains=[OrchestrationDomain.PRODUCT],
        product_state=ProductState(domain_status=DomainWorkflowStatus.SUSPENDED),
        order_state=OrderState(domain_status=DomainWorkflowStatus.IN_PROGRESS)
    )
    
    # 2. Supervisor transition back to PRODUCT
    decision = Supervisor.decide(
        "product",
        "product_question",
        state,
        "wait back to my phone"
    )
    new_state = Supervisor.apply_decision(state, decision)
    
    assert new_state.active_domain == OrchestrationDomain.PRODUCT.value
    assert OrchestrationDomain.ORDER in new_state.suspended_domains
    assert OrchestrationDomain.PRODUCT not in new_state.suspended_domains
    
    # 3. Persistence round trip
    serialized = new_state.model_dump_json()
    reloaded = WorkflowState.model_validate_json(serialized)
    
    assert reloaded.active_domain == OrchestrationDomain.PRODUCT
    assert OrchestrationDomain.ORDER in reloaded.suspended_domains
    assert reloaded.order_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert reloaded.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
