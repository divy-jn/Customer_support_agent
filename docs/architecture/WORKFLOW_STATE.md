# Workflow State Architecture

## Structured State Definition

The target supervisor/workflow state will be a strictly typed data structure (e.g., Pydantic or TypedDict for LangGraph) that maintains the transactional lifecycle of a customer interaction. 

### Core State Fields

```python
class TargetAgentState(TypedDict):
    # Session & Identity (Source of truth: WebSocket/Middleware auth)
    session_id: str
    customer_id: int | None
    customer_name: str | None
    
    # Semantic Routing (Source of truth: Semantic Router)
    current_domain: str | None
    current_intent: str | None
    sentiment: str | None
    urgency: str | None
    requires_human: bool
    
    # Conversation Context
    message: str
    conversation_history: list[dict]
    
    # Workflow Execution (Source of truth: Domain Agents & Supervisor)
    active_workflow: str | None       # e.g., "warranty_claim", "order_cancellation"
    workflow_status: str | None       # "in_progress", "awaiting_input", "completed", "failed"
    collected_entities: dict          # e.g., {"order_id": 1234}
    
    # Action & Approval State (Transaction DB / Redis)
    pending_action: dict | None       # Details of the action requiring approval
    approval_status: str | None       # "pending", "approved", "rejected"
    
    # Escalation & Ticketing Context (Source of truth: Guardian / Ticket DB)
    escalation_status: str | None     # "none", "requested", "escalated"
    active_ticket_references: list[int]
    recent_ticket_references: list[int]
    previous_attempts: int
    
    # Execution Results
    tool_results: list[dict]
    structured_outcome: dict | None   # Factual outcome before response generation
    response_state: str | None        # The final user-facing text
    safety_flags: list[str]
```

## State Distribution Strategy

Transactional workflow state must not live primarily in a vector database.

1. **Relational/Transactional DB (PostgreSQL)**:
   - Stores durable domain entities (Orders, Customers, Tickets).
   - Stores long-term Chat History (`conversations` table).
   - *Reasoning*: Strong consistency, ACID properties, required for auditing and business logic.
   
2. **LangGraph State / Checkpointing (PostgreSQL/Redis)**:
   - Stores the active `TargetAgentState` during an ongoing session.
   - Manages workflow pausing (e.g., waiting for high-risk action approval).
   - *Reasoning*: LangGraph natively supports checkpointing. Required for multi-turn workflows, intermediate tool results, and pausing/resuming graph execution.

3. **Cache (Redis)**:
   - Rate limiting, active connection tracking, ephemeral session locks.
   - *Reasoning*: Low-latency ephemeral storage.

4. **Vector Store (Pinecone)**:
   - Stores knowledge base chunks and historical similarity embeddings.
   - *Reasoning*: Purely for semantic search/RAG capabilities. Should *never* store active session workflow state or user transaction details.
