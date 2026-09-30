# Phase F.2.5 Discovery: Ticket-Context Migration

## Objective
Migrate the ticket-context boundary so domain agents communicate with the `TicketLifecycleService` through a newly defined, strictly typed `TicketContext`. This ensures that domain agents remain responsible for building the context, while the `TicketLifecycleService` retains absolute authority over ticket policy, without compromising domain state boundaries (e.g., `ProductAgent` passing an `order_id` does not mean it mutates `OrderState`).

## 1. Final TicketContext Design

We will introduce a bounded typed `TicketContext` to replace the transient and overly broad `IssueContext`.

```python
class TicketContext(BaseModel):
    # Customer Perimeter (Absolute)
    customer_id: int
    
    # Semantic Context
    domain: OrchestrationDomain
    intent: str
    message: str
    urgency: str
    sentiment: str
    
    # Domain Facts (Transient context for lifecycle, NOT state mutation)
    order_id: int | None = None
    product_name: str | None = None
    
    # Active Workflow Identity
    active_ticket_id: int | None = None
```

**Distinction of Fields:**
- **Authoritative domain facts**: Stored securely in `ProductState`, `OrderState`, etc.
- **Ticket identity/context**: Constructed temporarily into `TicketContext` by the Domain Agent for the lifecycle service.
- **Transient compatibility input**: Fields like `order_id` inside a `ProductAgent` turn. The `ProductAgent` uses it to populate `TicketContext.order_id` for deduplication, but *cannot* persist it to `OrderState`.

## 2. Ownership Matrix

| Component | Responsibility / Ownership |
| :--- | :--- |
| **WorkflowState (Root)** | Orchestration boundaries, globally shared context. `active_ticket_id` exists here *only* as a legacy compatibility projection. |
| **ProductState** | Product domain facts + authoritative `active_ticket_id` for the product workflow. |
| **OrderState** | Order domain facts + authoritative `active_ticket_id` for the order workflow. |
| **PaymentState** | Payment domain facts + authoritative `active_ticket_id` for the payment workflow. |
| **Domain Agents** | Extract domain facts, construct `TicketContext`, invoke `TicketLifecycleService`, and persist the resulting ticket ID into their *own* domain state (e.g., `ProductState.active_ticket_id`). |
| **TicketLifecycleService** | Absolute authority over ticket rules: issue identity, deduplication, CREATE/UPDATE operations, ambiguity resolution, customer isolation, and failure semantics. |
| **Supervisor** | Orchestrates workflow transitions (`active_domain`, suspension/resume). It does *not* manage ticket business policies. |

### Multi-Domain Isolation Example
If a customer is in the Product workflow (`ProductState.active_ticket_id = 701`) and switches to checking an order (`Supervisor` switches `active_domain` to `order`), the `OrderAgent` will interact with `TicketLifecycleService` independently. It will receive or create its own ticket (`OrderState.active_ticket_id = 702`). Because ticket ownership is domain-local, the Product ticket (701) is suspended safely and not overwritten by the Order ticket (702). A unified root ticket ID would incorrectly overwrite 701, breaking issue continuity.

## 3. Ticket Timing Decision

**Current Ordering:**
`Extract` → `TicketLifecycleService` → `Skill Validation` → `Skill/Tool Execution` → `Response`

**Analysis:**
According to the `TICKET_LIFECYCLE.md` contract, "Every distinct customer issue is tracked via a ticket." The ticket must act as an audit trail of the customer's request. 
If we invoke the lifecycle service *after* tool execution, a crash or unhandled failure during tool execution would result in the customer's issue never being logged. 
By invoking it *before* skill validation and tool execution (immediately after extraction), we guarantee **Issue Registration**. Even if the skill validation returns `AWAITING_INPUT`, the ticket securely absorbs the partial information. On the next turn, the `active_ticket_id` ensures the new inputs update the exact same ticket.

**Decision:**
The current timing (`Extract` → `TicketLifecycleService`) is architecturally correct for Issue Registration and must be preserved. Domain validation and resolution happen *after* registration. 

## 4. Ticket Failure and Reusability Rules

### Failure Rule
If `TicketLifecycleService` later returns `FAILED`:
- The domain agent must not silently treat the issue as successfully registered.
- The workflow must not falsely claim a ticket exists.
- Exact customer-facing recovery behavior will be decided in F.2.5.3.

### Closed-Ticket Rule
Closed tickets are not reusable for new issue matching.
(They are excluded from candidate lookup, and a new compatible issue may CREATE a new ticket).

## 5. `active_ticket_id` Strategy

- `ProductState.active_ticket_id`, `OrderState.active_ticket_id`, and `PaymentState.active_ticket_id` are the **authoritative** source of truth for their respective domain workflows.
- `WorkflowState.active_ticket_id` remains strictly as a **legacy compatibility field**. It must not be used as an alternative authoritative source. Historically, explicit adapters (`to_legacy_projection`, retired in C.2) populated it only for unmigrated consumers. 

## 5. Migration Sequence

The migration will be executed in the following strict, incremental slices:

1. **F.2.5.1: Typed TicketContext Contract**: Define the `TicketContext` model in `models.py`.
2. **F.2.5.2: TicketLifecycleService Migration**: Update `TicketLifecycleService` to accept `TicketContext` instead of `IssueContext`.
3. **F.2.5.3: ProductAgent Migration**: Update `ProductAgent` to construct and pass `TicketContext`, rigorously isolating transient facts (e.g. `order_id`) from domain state mutation.
4. **F.2.5.4: Legacy Compatibility Projection**: Ensure `WorkflowState.to_legacy_projection()` correctly handles `active_ticket_id` without corrupting domain bounds (Note: `to_legacy_projection` was subsequently retired in C.2).
5. **F.2.5.5: Regression and Lifecycle Tests**: Execute the test matrix and assert isolation boundaries.
6. **F.2.5.6: Order/Payment Preparation**: Scaffold the domain agent implementations for the remaining verticals based on the finalized F.2.5 contract.

## 6. Test Matrix

Required tests for F.2.5 acceptance:
1. Product ticket CREATE
2. Product ticket UPDATE
3. Product new issue → CREATE
4. Product same issue → UPDATE
5. `ProductState.active_ticket_id` is authoritative
6. Root `active_ticket_id` is compatibility-only
7. `OrderState.active_ticket_id` is independent
8. `PaymentState.active_ticket_id` is independent
9. Product ticket cannot overwrite Order ticket
10. Order ticket cannot overwrite Product ticket
11. Customer isolation (cross-customer merge rejected)
12. Closed tickets are not reusable for new issue matching (excluded from candidate lookup)
13. Ambiguous candidates (defaults to CREATE)
14. DB lookup failure (handled gracefully)
15. CREATE failure
16. UPDATE failure
17. Transient `order_id` ticket context (ProductAgent provides order_id without saving to OrderState)
18. `ProductAgent` cannot mutate `OrderState`
19. JSON serialization round-trip for `TicketContext`
20. Legacy projection behavior
21. Concurrent find→create limitation remains documented

## 7. Risks
- **Cross-Domain Misrouting**: Unmigrated legacy graphs might still read the root `active_ticket_id` and attach unrelated messages to it if the projection is flawed.
- **Schema Validation Issues**: Ensuring `TicketContext` strictly enforces validation without breaking relaxed legacy inputs.

## 8. Rollback Strategy
- The F.2.5 changes are confined to `ProductAgent`, `TicketLifecycleService`, and `models.py`.
- If critical regressions occur, the changes can be isolated and reverted via standard git rollback of the F.2.5 commits. Unmigrated agents (`db_agent.py`, `rag_agent.py`) remain completely insulated through the legacy projection adapter.

## 9. Open Questions
- When an issue is successfully resolved by a tool, should the Domain Agent explicitly invoke a ticket closure, or is that reserved for a later `ResolutionAgent` or manual human action? (Current behavior leaves it open for human review or auto-close policies).
