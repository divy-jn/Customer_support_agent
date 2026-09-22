# Workflow State Contract

## Architecture Boundary
- **PostgreSQL**: Durable business state (customers, orders, tickets, payments) AND durable LangGraph workflow checkpoints.
- **Vector Store**: Knowledge retrieval ONLY.
- **Redis**: Optional cache / ephemeral coordination only (not for source of truth of workflow state).

## Contract: `TargetAgentState`

The workflow state is strongly typed and holds only the data needed for coordination.

```python
class WorkflowStatus(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

class TargetAgentState(TypedDict):
    # Identity
    session_id: str
    customer_id: int | None
    
    # Semantic Context
    current_domain: str | None
    current_intent: str | None
    sentiment: str | None
    urgency: str | None
    
    # Workflow Lifecycle
    active_workflow: str | None
    workflow_status: WorkflowStatus | None
    pending_action: dict | None
    
    # Structured Data
    collected_entities: dict
    escalation_status: str | None
    turn_metadata: dict
    
    # Results
    last_meaningful_result: str | None
    router_diagnostics: dict | None
```

## Ephemeral vs Durable
- **Durable (via LangGraph Checkpoints in Postgres)**: `session_id`, `active_workflow`, `pending_action`, `collected_entities`.
- **Ephemeral**: Transcripts of the entire conversation shouldn't be duplicated in this dictionary state; they are stored in the DB `conversations` table or standard LangGraph message lists.
