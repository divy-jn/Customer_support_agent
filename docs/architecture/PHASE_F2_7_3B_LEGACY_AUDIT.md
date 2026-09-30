# Phase F.2.7.3B Legacy Consumer Audit

## 1. Executive Summary
This document summarizes the exhaustive architecture discovery of legacy `WorkflowState` root fields. The purpose of this audit is to identify all remaining production runtime consumers of the twelve legacy fields and outline a migration or deletion strategy for Phase F.2.7.3C. 

**Graphify Results:** The Graphify update was successfully validated, showing an absence of hidden side-channel consumers. Deep regex and AST-style searches confirmed that NO runtime execution path in the current multi-agent graph (Supervisor, Intent Router, Product Agent, Graph bindings) directly reads or mutates the legacy fields.

## 2. Field-by-Field Audit Matrix

| Legacy Field | Runtime Reads | Runtime Writes | Adapter/Persistence | Tests Only | Dead/Dormant | Typed Replacement | Status |
|--------------|---------------|----------------|---------------------|------------|--------------|-------------------|--------|
| `semantic_intent` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | Graph `intent` / Typed context | COMPATIBILITY-ONLY |
| `skill_name` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | None (Ephemeral context) | COMPATIBILITY-ONLY |
| `skill_version` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | None (Ephemeral context) | COMPATIBILITY-ONLY |
| `product_id` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `ProductState.product_id` | COMPATIBILITY-ONLY |
| `product_name` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `ProductState.product_name` | COMPATIBILITY-ONLY |
| `order_id` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `OrderState.order_id` | COMPATIBILITY-ONLY |
| `manufacturer` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `ProductState.manufacturer` | COMPATIBILITY-ONLY |
| `active_ticket_id` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `<Domain>State.active_ticket_id` | COMPATIBILITY-ONLY |
| `last_tool` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `<Domain>State.last_tool` | COMPATIBILITY-ONLY |
| `last_tool_result` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `<Domain>State.last_tool_result` | COMPATIBILITY-ONLY |
| `workflow_status` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | `global_status` + `domain_status` | COMPATIBILITY-ONLY |
| `pending_input` | 0 | 0 | Yes (`from_legacy`, `to_legacy`) | Yes | No | Handled via graph state/AWAITING_INPUT | COMPATIBILITY-ONLY |

## 3. Detailed Field Analysis

### 3.1 `workflow_status`
- **Runtime Consumers:** 0. 
- **Proof:** The `Supervisor` module (`supervisor.py`) no longer imports `WorkflowStatus` and makes no reference to `workflow_status`. All transitions properly manipulate `global_status` and `domain_status`.
- **Status:** COMPATIBILITY-ONLY.

### 3.2 `active_ticket_id`
- **Runtime Consumers:** 0.
- **Proof:** All instances of ticket tracking rely on `ProductState.active_ticket_id`, `OrderState.active_ticket_id`, and `PaymentState.active_ticket_id` (e.g. `state.product_state.active_ticket_id` in `ProductAgent`).
- **Status:** COMPATIBILITY-ONLY.

### 3.3 `product_id` / `product_name` / `manufacturer`
- **Runtime Consumers:** 0.
- **Proof:** Agents read the strictly typed `ProductState`. References found in `db_agent.py` and `lifecycle.py` use local dictionaries and query arguments, completely decoupled from `WorkflowState`.
- **Status:** COMPATIBILITY-ONLY.

### 3.4 `order_id`
- **Runtime Consumers:** 0.
- **Proof:** ProductAgent checks `merged_state.order_state.order_id` without falling back to the root `order_id`. `tools.py` uses `order_id` strictly as an RPC parameter.
- **Status:** COMPATIBILITY-ONLY.

### 3.5 `semantic_intent`
- **Runtime Consumers:** 0.
- **Proof:** The value is maintained epidemically in the LangGraph internal state `state["intent"]` and explicitly provided as a parameter to the Supervisor and ProductAgent logic via the current turn's `ProductContext`. It does not bleed into persisted state requirements.
- **Status:** COMPATIBILITY-ONLY.

### 3.6 `skill_name` / `skill_version`
- **Runtime Consumers:** 0.
- **Proof:** Loaded locally via `SkillRegistry` and logged purely within local function calls inside `product_agent.py` and `runtime.py`. It is not retrieved from or stored into `WorkflowState`.
- **Status:** COMPATIBILITY-ONLY.

### 3.7 `last_tool` / `last_tool_result`
- **Runtime Consumers:** 0.
- **Proof:** Evaluated exclusively as `state.product_state.last_tool` inside `ProductAgent` to feed past execution state.
- **Status:** COMPATIBILITY-ONLY.

### 3.8 `pending_input`
- **Runtime Consumers:** 0.
- **Proof:** No agent leverages `pending_input`. LangGraph native `pending_approval` state handles input suspension. 
- **Status:** COMPATIBILITY-ONLY.

## 4. Hidden Dictionary Consumers Check
Scanned explicitly for dynamic fetches (`["workflow_status"]`, `.get("product_id")`).
- **Findings:** `ticket.get("order_id")` exists inside Zendesk/Ticket lifecycles, and `params["order_id"]` exists inside `db_agent.py`, but these act strictly against standalone data models/API structures. 
- **Conclusion:** There are NO hidden dictionary accesses mutating `WorkflowState`.

## 5. Architectural Decision
All twelve legacy fields have **ZERO runtime consumers**.

They strictly exist as **Category B** definitions (Persistence / Serialization Compatibility Boundary) heavily intertwined with `WorkflowState.from_legacy()` and `to_legacy_projection()`. 

Because F.2.7.3A validated seamless round-tripping for older databases containing F.2.6 schemas, blindly deleting these fields now would break validation schemas when loading older persistence blobs unless a dynamic `model_validator(mode='before')` absorbs them seamlessly.

### Proposed Order of Migration Slices (For Phase F.2.7.3C)
To minimize compatibility risk, we recommend the following migration slices:

1. **Slice 1 (The Payload Cleaners):** Introduce a pre-validator on `WorkflowState` to absorb unmapped legacy fields gracefully without explicitly tracking them in Pydantic models. Ensure tests cover deserialization of pure legacy F.2.6 payloads into strictly F.2.7 states.
2. **Slice 2 (Remove Deprecated Logic):** Remove `to_legacy_projection()` entirely (as no new outgoing legacy projections are technically required if clients tolerate F.2.7 layout).
3. **Slice 3 (Field Deletion):** Physically remove the 12 fields from `WorkflowState` properties. Remove `from_legacy()` logic now fully covered by the root pre-validator.
