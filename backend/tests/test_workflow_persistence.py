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
    state = WorkflowState(session_id="test")
    state.product_state = ProductState()
    state.order_state = OrderState()
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

def test_legacy_projection_remains_deterministic_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701, product_name="Phone")
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    proj = restored.to_legacy_projection()
    assert proj["active_ticket_id"] == 701
    assert proj["product_name"] == "Phone"
    assert proj.get("order_id") is None

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

def test_product_ticket_does_not_leak_into_order_projection_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.ORDER.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    proj = restored.to_legacy_projection()
    assert proj["active_ticket_id"] == 702
    
def test_order_ticket_does_not_leak_into_product_projection_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    proj = restored.to_legacy_projection()
    assert proj["active_ticket_id"] == 701

def test_payment_ticket_does_not_leak_into_product_projection_after_round_trip():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.payment_state = PaymentState(active_ticket_id=703)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    dumped = json.loads(state.model_dump_json())
    restored = WorkflowState.from_legacy(dumped)
    proj = restored.to_legacy_projection()
    assert proj["active_ticket_id"] == 701

def test_malformed_persistence_rejects_general_with_domain_states():
    with pytest.raises(ValueError, match='Domain general cannot own typed domain states'):
        WorkflowState.model_validate({
            'session_id': 'test',
            'active_domain': 'general',
            'product_state': {'domain_status': 'in_progress'}
        })

def test_malformed_persistence_rejects_escalation_with_domain_states():
    with pytest.raises(ValueError, match='Domain escalation cannot own typed domain states'):
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

