# Migration Plan (Phase A to H)

## Phased Approach

### Phase A: Architecture Discovery
- **Goal**: Analyze current monolithic agents, map to target domains, define contracts.
- **Status**: Completed (This document set).
- **Dependencies**: None.
- **Rollback**: None required (no code changes).

### Phase B: State & Contracts
- **Goal**: Implement the new `TargetAgentState` in LangGraph and the new Semantic Router contract.
- **Strategy**: Run the new Router in shadow mode alongside the existing `intent_router`. Log outputs and compare accuracy.
- **Dependencies**: Database schemas for state (if checkpointing requires migration).
- **Status**: 
  - PHASE B: ARCHITECTURALLY COMPLETE
  - REGRESSION: 176 passed / 17 environment-blocked
  - PHASE B.1 HARDENING: PASS
  - KNOWN ENVIRONMENT DEPENDENCY: OpenAI authentication/configuration for live LLM-backed tests

### Phase C: Skills Runtime
- **Goal**: Implement the base `Skill` execution engine (SOP parsing, context injection).
- **Strategy**: Build the engine without connecting it to live traffic. Write unit tests for the SOP runner.

### Phase D: Domain Agents (General, Product, Order, Payment)
- **Goal**: Scaffold the 4 core domain agents and migrate the tools.
- **Strategy**: 
  - Expose tools from `tools.py` to the new Domain Agents.
  - Test via Golden Dataset strictly in CI/CD.
  
### Phase E: Warranty Vertical Slice (First Production Flow)
- **Goal**: Implement the end-to-end `Product -> Warranty` workflow.
- **Strategy**: 
  - Route *only* product warranty intents to the new architecture using a feature flag.
  - **Evaluation Gate**: Must pass 100% of Warranty subset in Golden Dataset.
  - **Graphify Update**: Required after merging.

### Phase F: Guardian / Escalation Policy
- **Goal**: Implement the sophisticated duplicate-ticket prevention and escalation logic.
- **Strategy**: Migrate `escalation_agent.py` to the Guardian model. Route all escalations through this new module.

### Phase G: Migration of Existing Flows
- **Goal**: Route remaining intents (billing, cancellation, tracking) to the new Order/Payment agents.
- **Strategy**: Incremental feature flags per intent. 
- **Rollback**: Toggle feature flag back to `db_agent` or `rag_agent`.

### Phase H: Deprecation
- **Goal**: Remove `db_agent.py`, `rag_agent.py`, `web_agent.py` and old graph nodes.
- **Strategy**: Once 100% of traffic is on the new architecture for 7 days with stable metrics, delete legacy files.
- **Graphify Update**: Re-run to establish the clean target graph.

## Rollback / Safety Strategy
During migration, we will use an **Architecture Mode Switch** (Feature Flag). The main FastAPI websocket entry point will inspect a customer-level or global feature flag.
- If `USE_V2_AGENT=False`, traffic routes to `customer_support_graph`.
- If `USE_V2_AGENT=True`, traffic routes to `domain_support_graph`.
This ensures the existing system remains untouched and fully runnable throughout the migration.
