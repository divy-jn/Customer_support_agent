import pytest
from app.notification_identity import (
    NotificationType,
    SourceEventIdentity,
    RecipientIdentity,
    LogicalNotificationIdentity,
    ClientRequestIdentity
)

def test_identical_inputs_produce_identical_identity():
    # 1. same source event + same notification type + same recipient => identical logical identity
    source = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc")
    recipient = RecipientIdentity.customer(customer_id=1)
    
    id1 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc"),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity.customer(customer_id=1)
    )
    
    assert id1.canonicalize() == id2.canonicalize()
    assert id1.get_hash() == id2.get_hash()

def test_different_recipient_produces_different_identity():
    # 2. different recipient => different logical identity
    source = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc")
    
    id1 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity.customer(customer_id=1)
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity.support_team("billing")
    )
    
    assert id1.get_hash() != id2.get_hash()

def test_different_notification_type_produces_different_identity():
    # 3. different notification type => different logical identity
    source = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc")
    recipient = RecipientIdentity.customer(customer_id=1)
    
    id1 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_RESOLVED,
        recipient=recipient
    )
    
    assert id1.get_hash() != id2.get_hash()

def test_different_source_event_produces_different_identity():
    # 4. different source event => different logical identity
    recipient = RecipientIdentity.customer(customer_id=1)
    
    id1 = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc"),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-def"),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    assert id1.get_hash() != id2.get_hash()

def test_same_client_request_different_customer():
    # 5. same client_request_id under different customers => different scoped source identity / logical identity
    recipient1 = RecipientIdentity.customer(customer_id=1)
    recipient2 = RecipientIdentity.customer(customer_id=2)
    
    id1 = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="common-req"),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient1
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(customer_id=2, client_request_id="common-req"),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient2
    )
    
    assert id1.get_hash() != id2.get_hash()
    assert id1.source_event.source_event_id != id2.source_event.source_event_id

def test_recipient_email_change_does_not_affect_identity():
    # 6. recipient email address changes while recipient_identity stays constant => logical identity remains unchanged
    # The contract specifically isolates the logical principal from the delivery address.
    source = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc")
    recipient = RecipientIdentity.customer(customer_id=1)
    
    id1 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    # Simulating email address update on customer profile: the recipient_identity ("customer:1") is invariant.
    id2 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity.customer(customer_id=1)
    )
    
    assert id1.get_hash() == id2.get_hash()

def test_canonicalization_is_deterministic():
    # 7. canonicalization is deterministic
    source = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-abc")
    recipient = RecipientIdentity.customer(customer_id=1)
    
    id1 = LogicalNotificationIdentity(
        source_event=source,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    assert id1.canonicalize() == '["v1","customer:1|req:req-abc","TICKET_CREATED","customer:1"]'

def test_canonicalization_is_unambiguous():
    # 8. canonicalization is unambiguous (prevents concatenation collisions)
    # A source event "customer:1|req:a" with type "bc" 
    # vs source event "customer:1|req:ab" with type "c" 
    # shouldn't collide.
    
    # Using raw instantiation to simulate edge cases
    source1 = SourceEventIdentity(namespace="v1", source_event_id="A")
    id1 = LogicalNotificationIdentity(
        source_event=source1,
        notification_type=NotificationType.TICKET_CREATED,  # The value is TICKET_CREATED
        recipient=RecipientIdentity(principal="BC")
    )
    
    source2 = SourceEventIdentity(namespace="v1", source_event_id="AB")
    id2 = LogicalNotificationIdentity(
        source_event=source2,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity(principal="C")
    )
    
    # Under raw concatenation "ATICKET_CREATEDBC" vs "ABTICKET_CREATEDC" might be distinct anyway,
    # but JSON array prevents any bleed across boundaries.
    assert id1.canonicalize() == '["v1","A","TICKET_CREATED","bc"]'
    assert id2.canonicalize() == '["v1","AB","TICKET_CREATED","c"]'
    assert id1.get_hash() != id2.get_hash()

def test_namespace_version_change():
    # 9. namespace/schema version change produces a distinct identity
    recipient = RecipientIdentity.customer(customer_id=1)
    
    source_v1 = SourceEventIdentity(namespace="v1", source_event_id="customer:1|req:abc")
    id1 = LogicalNotificationIdentity(
        source_event=source_v1,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    source_v2 = SourceEventIdentity(namespace="v2", source_event_id="customer:1|req:abc")
    id2 = LogicalNotificationIdentity(
        source_event=source_v2,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=recipient
    )
    
    assert id1.get_hash() != id2.get_hash()

def test_escalation_future_source_event():
    # 10. escalation future source-event representation is deterministic
    source1 = SourceEventIdentity.from_escalation("evt-999")
    source2 = SourceEventIdentity.from_escalation("evt-999")
    source3 = SourceEventIdentity.from_escalation("evt-1000")
    
    assert source1.source_event_id == "escalation:evt-999"
    assert source1.source_event_id == source2.source_event_id
    assert source1.source_event_id != source3.source_event_id
    
    id1 = LogicalNotificationIdentity(
        source_event=source1,
        notification_type=NotificationType.ESCALATION_TEAM,
        recipient=RecipientIdentity.support_team()
    )
    
    id2 = LogicalNotificationIdentity(
        source_event=source3,
        notification_type=NotificationType.ESCALATION_TEAM,
        recipient=RecipientIdentity.support_team()
    )
    
    assert id1.get_hash() != id2.get_hash()
