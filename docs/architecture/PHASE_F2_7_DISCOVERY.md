# PHASE F.2.7 - Legacy Field Removal Discovery

## Overview

This discovery phase audits the legacy flat fields on `WorkflowState` to determine their usage patterns and exact prerequisites for safe deletion. 

While Phase F.2.5 successfully migrated `ProductAgent` to the F.2 `TicketContext` and F.2 domain states, **`ProductAgent` currently still writes back to these legacy root fields** via `_apply_legacy_projection()` before returning the state. These fields are subsequently persisted to Redis by `chat_handler.py`.

**No fields can be safely deleted immediately.** Each legacy field serves as a compatibility boundary for unmigrated components (like `graph.py` and `OrderAgent`). 

---

## 1. `active_ticket_id`

- **Location:** `WorkflowState.active_ticket_id`
- **Authoritative Counterpart:** `ProductState.active_ticket_id` (and future `OrderState`/`PaymentState`).
- **Current Producers:** `chat_handler.py`, F.2 agents (historically via `to_legacy_projection()`, which was retired in C.2).
- **Current Consumers:** 
  - `graph.py` (line 424): The LangGraph router `route_after_classification` explicitly checks `ws_dict.get("active_ticket_id")` to maintain domain affinity across turns.
  - `models.py`: Used in `from_legacy()` to fall back if domain states are absent.
- **Prerequisites for Removal:**
  - **F.2.5.6 (Order/Payment Migration):** All agents must manage tickets in their respective typed domain states.
  - **F.2.8 (Graph Modernization):** `graph.py` MUST be updated to check `domain_state.active_ticket_id` instead of the legacy root field.

## 2. `workflow_status`

- **Location:** `WorkflowState.workflow_status`
- **Authoritative Counterpart:** `WorkflowState.global_status` and `ProductState.domain_status`.
- **Current Producers:** `chat_handler.py` (initialization to "idle"), F.2 agents (via projection).
- **Current Consumers:**
  - `graph.py` (line 422): Router checks `ws_dict.get("workflow_status")` against `"awaiting_input"` or `"in_progress"` to enforce routing continuity.
- **Prerequisites for Removal:**
  - **F.2.8 (Graph Modernization):** The legacy LangGraph router must transition to F.2 rules, evaluating `global_status` and individual `domain_status` instead of `workflow_status`.

## 3. `semantic_intent`

- **Location:** `WorkflowState.semantic_intent`
- **Authoritative Counterpart:** LangGraph `AgentState["intent"]` (runtime) and F.2 `TicketContext.intent` (domain boundary).
- **Current Consumers:**
  - Largely dormant on `WorkflowState`. `ProductDomainContext` uses `state["intent"]` directly from LangGraph. 
  - `db_agent` relies on `AgentState["intent"]`.
- **Prerequisites for Removal:**
  - Can be removed from `WorkflowState` now that `to_legacy_projection` is deprecated (C.2), but the broader concept relies on F.3 (Semantic Router Replacement) to fully eliminate the legacy string-based intent passing.

## 4. `order_id`

- **Location:** `WorkflowState.order_id`
- **Authoritative Counterpart:** `OrderState.order_id`.
- **Current Consumers:**
  - **`ProductAgent` explicitly uses it!** (lines 210, 499): `ProductAgent` checks `merged_state.order_id` as a fallback if `order_state.order_id` is missing.
  - `db_agent` uses its own LLM-extracted `order_id`, NOT the `WorkflowState` root field.
- **Prerequisites for Removal:**
  - **F.2.5.6 (Order Migration):** `OrderAgent` must migrate to `OrderState`.
  - `ProductAgent` MUST drop the root fallback logic, relying strictly on `OrderState`.

## 5. `product_id`, `product_name`, `manufacturer`

- **Location:** `WorkflowState.product_id`, etc.
- **Authoritative Counterpart:** `ProductState`.
- **Current Consumers:**
  - `from_legacy()` relies on these to construct an initial `ProductState` for unmigrated legacy payloads.
- **Prerequisites for Removal:**
  - Complete elimination of the `from_legacy()` fallback reconstruction logic once all legacy persisted sessions have naturally expired from Redis.

## 6. `skill_name`, `skill_version`, `last_tool`, `last_tool_result`

- **Location:** `WorkflowState.skill_name`, etc.
- **Authoritative Counterpart:** `ProductState.last_tool`, etc.
- **Current Consumers:**
  - `ProductAgent` has successfully migrated to `product_state.last_tool` and `product_state.last_tool_result`.
- **Prerequisites for Removal:**
  - **F.2.5.6 (Order/Payment Migration):** `OrderAgent` and `PaymentAgent` must migrate their skill execution trackers to their respective F.2 domain states.

## 7. `pending_input`

- **Location:** `WorkflowState.pending_input`
- **Authoritative Counterpart:** LangGraph `AgentState["pending_approval"]` and UI interaction boundaries.
- **Prerequisites for Removal:**
  - **F.8 (UI Boundary):** Complete migration to the standardized F.8 architectural patterns for interactive input.

---

## Conclusion & Next Steps

This discovery confirms the mandate constraint: **Do NOT assume all of these are removable together.** 

Specifically, `workflow_status` and `active_ticket_id` are deeply entangled with the F.1 `graph.py` router logic, while `order_id` and skill-related fields are dependent on migrating the remaining domain agents (F.2.5.6).

**Immediate Next Step:** Proceed to F.2.5.5 (Regression and Lifecycle Tests) and F.2.5.6 (OrderAgent and PaymentAgent migration) to unblock the eventual deletion of these legacy fields.
