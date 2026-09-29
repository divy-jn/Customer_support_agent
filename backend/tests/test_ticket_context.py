import pytest
from pydantic import ValidationError
from app.models import TicketContext, OrchestrationDomain, Urgency, Sentiment

def test_ticket_context_product_domain():
    ctx = TicketContext(
        customer_id=123,
        domain=OrchestrationDomain.PRODUCT,
        intent="warranty_claim",
        message="My phone screen is broken.",
        product_name="iPhone 14"
    )
    assert ctx.customer_id == 123
    assert ctx.domain == OrchestrationDomain.PRODUCT
    assert ctx.intent == "warranty_claim"
    assert ctx.message == "My phone screen is broken."
    assert ctx.product_name == "iPhone 14"
    assert ctx.order_id is None
    assert ctx.active_ticket_id is None
    assert ctx.urgency == Urgency.MEDIUM
    assert ctx.sentiment == Sentiment.NEUTRAL

def test_ticket_context_order_domain():
    ctx = TicketContext(
        customer_id=456,
        domain=OrchestrationDomain.ORDER,
        intent="order_status",
        message="Where is my stuff?",
        order_id=789,
        active_ticket_id=42
    )
    assert ctx.domain == OrchestrationDomain.ORDER
    assert ctx.order_id == 789
    assert ctx.active_ticket_id == 42

def test_ticket_context_payment_domain():
    ctx = TicketContext(
        customer_id=789,
        domain=OrchestrationDomain.PAYMENT,
        intent="refund_request",
        message="I was charged twice.",
        urgency=Urgency.HIGH,
        sentiment=Sentiment.NEGATIVE
    )
    assert ctx.domain == OrchestrationDomain.PAYMENT
    assert ctx.urgency == Urgency.HIGH
    assert ctx.sentiment == Sentiment.NEGATIVE

def test_ticket_context_serialization_roundtrip():
    original = TicketContext(
        customer_id=111,
        domain=OrchestrationDomain.PRODUCT,
        intent="technical_support",
        message="It won't turn on.",
        urgency=Urgency.CRITICAL,
        sentiment=Sentiment.NEGATIVE,
        order_id=222,
        product_name="Laptop",
        active_ticket_id=333
    )
    
    # dict -> dict
    dumped_dict = original.model_dump(mode="json")
    assert isinstance(dumped_dict["domain"], str)
    assert isinstance(dumped_dict["urgency"], str)
    
    reconstructed_dict = TicketContext.model_validate(dumped_dict)
    assert reconstructed_dict == original
    
    # json -> json
    dumped_json = original.model_dump_json()
    reconstructed_json = TicketContext.model_validate_json(dumped_json)
    assert reconstructed_json == original

def test_invalid_domain():
    with pytest.raises(ValidationError) as exc:
        TicketContext(
            customer_id=1,
            domain="invalid_domain",
            intent="test",
            message="test message"
        )
    assert "Input should be" in str(exc.value)

def test_invalid_identifiers():
    with pytest.raises(ValidationError):
        TicketContext(
            customer_id=-1, # Must be gt=0
            domain=OrchestrationDomain.PRODUCT,
            intent="test",
            message="msg"
        )
        
    with pytest.raises(ValidationError):
        TicketContext(
            customer_id=1,
            domain=OrchestrationDomain.PRODUCT,
            intent="test",
            message="msg",
            order_id=0 # Must be gt=0
        )
        
    with pytest.raises(ValidationError):
        TicketContext(
            customer_id=1,
            domain=OrchestrationDomain.PRODUCT,
            intent="test",
            message="msg",
            active_ticket_id=-5 # Must be gt=0
        )

def test_excessive_string_lengths():
    with pytest.raises(ValidationError):
        TicketContext(
            customer_id=1,
            domain=OrchestrationDomain.PRODUCT,
            intent="A" * 300, # Max 255
            message="msg"
        )
        
    with pytest.raises(ValidationError):
        TicketContext(
            customer_id=1,
            domain=OrchestrationDomain.PRODUCT,
            intent="test",
            message="M" * 15000 # Max 10000
        )

def test_domain_isolation_boundary():
    """
    DOCUMENTATION/ASSERTION TEST:
    TicketContext may contain an order_id while domain=PRODUCT because order_id 
    is ticket identity/context. This must NOT imply ProductAgent -> OrderState mutation.
    TicketContext is a transient DTO.
    """
    ctx = TicketContext(
        customer_id=10,
        domain=OrchestrationDomain.PRODUCT,
        intent="warranty_claim",
        message="I bought this laptop yesterday and it's dead.",
        order_id=999 # Transient context for lifecycle deduplication
    )
    
    assert ctx.domain == OrchestrationDomain.PRODUCT
    assert ctx.order_id == 999
    
    # Asserting the absence of actual domain states on the DTO.
    # It does not contain an OrderState object, so it cannot mutate it.
    assert not hasattr(ctx, "order_state")
    assert not hasattr(ctx, "product_state")

def test_optional_fields():
    ctx = TicketContext(
        customer_id=1,
        domain=OrchestrationDomain.GENERAL,
        intent="inquiry",
        message="Hello?"
    )
    assert ctx.order_id is None
    assert ctx.product_name is None
    assert ctx.active_ticket_id is None
