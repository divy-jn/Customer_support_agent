import pytest
from pydantic import ValidationError
from app.models import (
    WorkflowState, 
    ProductState, 
    OrderState, 
    PaymentState,
    OrchestrationDomain, 
    GlobalWorkflowStatus, 
    DomainWorkflowStatus
)

def test_valid_construction():
    """Test valid construction with F.2 fields."""
    state = WorkflowState(
        session_id="test_session",
        customer_id=123,
        active_domain=OrchestrationDomain.PRODUCT,
        product_state=ProductState(
            domain_status=DomainWorkflowStatus.IN_PROGRESS,
            product_id=1,
            product_name="Phone",
            active_ticket_id=999
        )
    )
    assert state.session_id == "test_session"
    assert state.active_domain == OrchestrationDomain.PRODUCT
    assert state.product_state is not None
    assert state.product_state.product_name == "Phone"

def test_bounded_strings():
    """Test string length bounds on domain states."""
    long_string = "a" * 256
    with pytest.raises(ValidationError) as exc_info:
        ProductState(product_name=long_string)
    assert "String should have at most 255 characters" in str(exc_info.value)
    
    with pytest.raises(ValidationError) as exc_info:
        OrderState(tracking_number=long_string)
    assert "String should have at most 255 characters" in str(exc_info.value)

def test_invalid_active_domain_state_combinations():
    """active_domain must have its corresponding domain_state."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            active_domain=OrchestrationDomain.PRODUCT,
            product_state=None  # Missing!
        )
    assert "PRODUCT domain is active but product_state is None" in str(exc_info.value)
    
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            active_domain=OrchestrationDomain.ORDER,
            order_state=None
        )
    assert "ORDER domain is active but order_state is None" in str(exc_info.value)
    
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            active_domain=OrchestrationDomain.PAYMENT,
            payment_state=None
        )
    assert "PAYMENT domain is active but payment_state is None" in str(exc_info.value)

def test_duplicate_suspended_domains():
    """Suspended domains cannot contain duplicates."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            suspended_domains=[OrchestrationDomain.PRODUCT, OrchestrationDomain.PRODUCT],
            product_state=ProductState()
        )
    assert "Duplicate suspended domains detected" in str(exc_info.value)

def test_missing_suspended_domain_state():
    """Suspended domains must have their corresponding domain_state."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            suspended_domains=[OrchestrationDomain.PRODUCT],
            product_state=None
        )
    assert "PRODUCT is suspended but state is missing" in str(exc_info.value)

def test_invalid_schema_version():
    """schema_version != 1 must be rejected."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=0
        )
    assert "Unsupported schema_version 0. Only version 1 is currently supported." in str(exc_info.value)
    
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=2
        )
    assert "Unsupported schema_version 2. Only version 1 is currently supported." in str(exc_info.value)

def test_negative_state_revision():
    """state_revision cannot be negative"""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            state_revision=-1
        )
    assert "Negative state_revision" in str(exc_info.value)

def test_domain_status_enum_validation():
    """Ensure enum boundaries."""
    # RESUMED is not a valid DomainWorkflowStatus
    with pytest.raises(ValidationError):
        ProductState(domain_status="resumed")

def test_serialization_deserialization_round_trip():
    """Test full dump and load."""
    original = WorkflowState(
        session_id="sess_123",
        customer_id=456,
        active_domain=OrchestrationDomain.ORDER,
        global_status=GlobalWorkflowStatus.IN_PROGRESS,
        suspended_domains=[OrchestrationDomain.PRODUCT],
        order_state=OrderState(
            domain_status=DomainWorkflowStatus.AWAITING_INPUT,
            order_id="ORD-123",
            tracking_number="TRK-999"
        ),
        product_state=ProductState(
            domain_status=DomainWorkflowStatus.SUSPENDED,
            product_id=55
        )
    )
    
    serialized = original.model_dump_json()
    deserialized = WorkflowState.model_validate_json(serialized)
    
    assert deserialized.session_id == original.session_id
    assert deserialized.active_domain == OrchestrationDomain.ORDER
    assert deserialized.suspended_domains == [OrchestrationDomain.PRODUCT]
    assert deserialized.order_state.order_id == "ORD-123"
    assert deserialized.product_state.domain_status == DomainWorkflowStatus.SUSPENDED

def test_one_state_per_domain_invariant():
    """
    Test that domains are strongly typed singletons in the global state,
    not dicts or lists of instances.
    """
    state = WorkflowState(
        session_id="test",
        product_state=ProductState(product_id=1),
        order_state=OrderState(order_id="O1"),
        payment_state=PaymentState(transaction_id="TX1")
    )
    # The attributes must be explicit Pydantic instances, not lists
    assert isinstance(state.product_state, ProductState)
    assert isinstance(state.order_state, OrderState)
    assert isinstance(state.payment_state, PaymentState)
    
    # Overwriting it replaces the singular instance (maintaining the "at most one" invariant)
    state.product_state = ProductState(product_id=2)
    assert state.product_state.product_id == 2

def test_suspended_domains_length_exceeded():
    """suspended_domains > 3 must be rejected."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(
            session_id="s1",
            schema_version=1,
            suspended_domains=[
                OrchestrationDomain.PRODUCT,
                OrchestrationDomain.ORDER,
                OrchestrationDomain.PAYMENT,
                OrchestrationDomain.GENERAL  # 4th domain
            ],
            product_state=ProductState(),
            order_state=OrderState(),
            payment_state=PaymentState()
        )
    # Pydantic built-in validation for max_length=3 might trigger first, or the custom validator.
    # The error string will contain "at most 3 items" (Pydantic standard) or our custom string.
    assert "3 items" in str(exc_info.value) or "cannot exceed 3" in str(exc_info.value)

def test_supported_active_domains_accept():
    """valid version + valid revision + valid nested states => accept"""
    state1 = WorkflowState(
        session_id="s1",
        schema_version=1,
        state_revision=0,
        active_domain=OrchestrationDomain.ORDER,
        order_state=OrderState(order_id="ord_1")
    )
    assert state1.active_domain == OrchestrationDomain.ORDER
    
    state2 = WorkflowState(
        session_id="s2",
        active_domain=OrchestrationDomain.PAYMENT,
        payment_state=PaymentState(transaction_id="txn_2")
    )
    assert state2.active_domain == OrchestrationDomain.PAYMENT
