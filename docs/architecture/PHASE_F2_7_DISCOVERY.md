# PHASE F.2.7 - Legacy Field Removal Discovery (Reconciled pre-C.3)

## Overview

This document reconciles the final audit of the 12 legacy flat fields on `WorkflowState` prior to their physical deletion in Phase F.2.7.3C.3.

Historically (pre-C.2), these fields served as compatibility boundaries. However, exhaustive runtime auditing of the current codebase confirms that **there are zero remaining runtime readers or writers of these Pydantic attributes.**

The outbound legacy projection (`to_legacy_projection`) was retired in Phase F.2.7.3C.2. The inbound adapter (`WorkflowState.from_legacy()`) operates purely on raw Python dictionaries when hydrating old payloads, meaning it does not rely on the `WorkflowState` Pydantic class actually possessing these fields.

---

## 1. `active_ticket_id`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** `DomainState.active_ticket_id`.
- **Historical Consumers (Pre-C.2):** `graph.py` router previously checked `ws_dict.get("active_ticket_id")`.
- **Current Reality:** `graph.py` now reconstructs typed state via `WorkflowState.from_legacy(ws_dict)` and checks `ws.product_state.active_ticket_id`. The root attribute is completely ignored.

## 2. `workflow_status`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** `WorkflowState.global_status` and `DomainState.domain_status`.
- **Historical Consumers (Pre-C.2):** `graph.py` previously checked `ws_dict.get("workflow_status")`.
- **Current Reality:** `graph.py` evaluates typed domain statuses (e.g., `ws.product_state.domain_status`). `WorkflowState.workflow_status` is untouched.

## 3. `semantic_intent`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** LangGraph `AgentState["intent"]`.
- **Current Reality:** Never read from `WorkflowState`. `from_legacy()` extracts it from the legacy dictionary to aid routing, but it is not needed on the Pydantic model.

## 4. `order_id`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** `OrderState.order_id`.
- **Historical Consumers (Pre-C.2):** `ProductAgent` historically checked `merged_state.order_id` as a fallback.
- **Current Reality:** `ProductAgent` explicitly uses `extracted_order_id` or `merged_state.order_state.order_id`. The root fallback logic was removed.

## 5. `product_id`, `product_name`, `manufacturer`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** `ProductState` fields.
- **Current Reality:** `from_legacy()` extracts these from legacy dictionaries to populate a typed `ProductState`. The Pydantic model itself no longer needs these attributes defined.

## 6. `skill_name`, `skill_version`, `last_tool`, `last_tool_result`
- **Current Runtime Status:** Safe to delete.
- **Authoritative Counterpart:** `DomainState.last_tool` and `DomainState.last_tool_result`.
- **Current Reality:** Completely unused at the root level. ProductAgent and Supervisor strictly interact with typed domain state fields.

## 7. `pending_input`
- **Current Runtime Status:** Safe to delete.
- **Current Reality:** Unused as a root property. Domain agents and the Supervisor rely on `AWAITING_INPUT` domain statuses instead of this string field.

---

## Conclusion & Next Steps

**Prerequisites for C.3 Deletion:** ALL MET.

The current codebase strictly relies on the typed `WorkflowState` hierarchy (`ProductState`, `OrderState`, `PaymentState`) and the explicit `from_legacy()` dictionary ingestion boundary. The 12 legacy fields only exist as empty structural remnants.

**Immediate Next Step:** Proceed to Phase F.2.7.3C.3 (Physical Deletion) to remove the 12 fields from `models.py` and finalize the persistence schema modernization.
