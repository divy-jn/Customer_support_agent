# Workflow State Contract

## Architecture Boundary
- **PostgreSQL**: Durable business state (customers, orders, tickets, payments) AND durable LangGraph workflow checkpoints.
- **Vector Store**: Knowledge retrieval ONLY.
- **Redis / In-Memory Store**: Ephemeral session bridging in the websocket handler.

## Contract: `WorkflowState`
The workflow state is strongly typed and holds only the data needed for orchestration.

```python
class WorkflowStatus(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

class WorkflowState(BaseModel):
    # Immutable Session Boundaries
    session_id: str
    customer_id: int | None = None
    
    # Semantic Context
    active_domain: str | None = None
    semantic_intent: str | None = None
    skill_name: str | None = None
    skill_version: str | None = None
    
    # Bounded Entities
    product_id: int | None = None
    product_name: str | None = None
    order_id: int | None = None
    manufacturer: str | None = None
    
    # Execution Tracking
    last_tool: str | None = None
    last_tool_result: ToolResultEnvelope | None = None
    
    # Lifecycle
    workflow_status: WorkflowStatus = WorkflowStatus.IDLE
    pending_input: str | None = None
    turn_count: int = 0
    updated_at: datetime | None = None
```

## State Security & Persistence Policies

1. **Active Workflow Continuation**
   If `workflow_status` is `AWAITING_INPUT` or `IN_PROGRESS`, the semantic router allows the previous domain agent to intercept the turn, preventing legacy routers from dropping context mid-flow.

2. **Provenance Gate**
   The LLM must flag updates as `source="USER_EXPLICIT"`. The Python boundary actively normalizes and validates that the explicit text physically exists in the customer message before accepting the field into state. `MODEL_INFERENCE` is categorically rejected.

3. **Stale Result Invalidation**
   If a parent entity (e.g., `manufacturer` or `order_id`) changes, the extraction phase strictly invalidates `last_tool` and `last_tool_result` to prevent stale data reuse.

4. **Bounded WorkflowState**
   The model uses explicit nullable fields (`order_id`, `manufacturer`). Arbitrary dynamic dictionaries (e.g. `entities: dict`) are strictly forbidden.

5. **Immutability of Session Identity**
   `session_id` and `customer_id` are authoritative parameters provided by middleware. The LLM is explicitly forbidden from extracting or overwriting them.

6. **State Merge Policy**
   New validated entities update the current `WorkflowState`. If a field is `None` in the new extraction but previously existed, the old value is safely preserved.

7. **Persistence Boundary**
   The boundary for WorkflowState persistence is strictly the `handle_customer_ws()` orchestrator via the `session_store`, completely decoupled from the domain agents' isolated business logic.
