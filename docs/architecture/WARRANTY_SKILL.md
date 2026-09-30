# Warranty Skill Architecture (Phase E.2)

## 1. Skill Ownership Boundaries
The `WarrantySkill` operates under the Domain-Oriented Multi-Agent Architecture. Its ownership boundaries are strictly defined:
*   **Semantic/Workflow Responsibility**: Owns the Standard Operating Procedure (SOP) for warranty inquiries. This includes guiding the customer through disambiguation, explaining warranty math, and providing next steps.
*   **Deterministic Policy Responsibility**: Declares allowed capabilities (`check_warranty_status`, `web_search`), explicitly forbids transactional capabilities (`create_ticket`, `process_refund`), and mandates a `READ_ONLY` risk profile.
*   **Capability/Tool Responsibility**: The skill *owns no execution logic*. Tools are decoupled, deterministic Python functions (e.g., `check_warranty_status`) owned by the backend.
*   **LLM Responsibility**: The LLM is responsible for reading the declarative `SKILL.md` SOP, extracting entities (Order ID, Product Name) from the conversation, interpreting the deterministic tool responses (`WarrantyStatusResult`), synthesizing a safe search query if the warranty is active, and generating a polite, human-readable response.

## 2. Warranty Skill Contract
*   **Domain**: `product`
*   **Version**: `1.0.0`
*   **Trigger Conditions**: 
    *   Customer asks about product warranty.
    *   Customer reports a broken/defective product and asks for repair options.
*   **Required Inputs**: `customer_id`, `query`
*   **Optional Inputs**: `order_id`, `product_name`
*   **Allowed Tools**: `check_warranty_status`, `web_search`
*   **Forbidden Tools**: `cancel_order`, `process_refund`, `create_ticket`, `update_ticket`
*   **Risk Level**: `READ_ONLY`
*   **Requires Confirmation**: `false`
*   **Expected Structured Output**: A synthesized natural language message directing the user appropriately based on their warranty status.
*   **Failure Behavior**: If tools fail or the manufacturer cannot be determined, gracefully advise the customer to consult the documentation that came with their product or contact the manufacturer directly.

## 3. Runtime Limitations (Pre-Implementation Gaps)
The generic `SkillRuntime` and `ProductAgent` currently have limitations that block the immediate implementation of `WarrantySkill`:
1.  **Single-Shot Tool Execution**: `ProductAgent` currently hardcodes the execution of `skill.policy.allowed_tools[0]` *before* invoking the LLM. It does not support ReAct-style dynamic multi-tool orchestration (i.e., calling `check_warranty_status`, analyzing the result, and then calling `web_search`).
2.  **Tool Registry Absence**: There is no centralized `ToolRegistry`. The `SkillRuntime` relies on a generic `ToolExecutor` protocol, and tools are globally exposed.
3.  **Result Payload Boundary**: The generic `{"tool_result": result}` payload does not cleanly expose the strongly-typed `WarrantyStatusResult` schema to the LLM context in a structured way.

**Requirement**: A ReAct/dynamic tool-calling loop must be implemented in the domain agent (or runtime) to support multi-turn, multi-tool skills before `WarrantySkill` can be wired in.

## 4. Manufacturer Identity Strategy
*   **Data Model Constraints**: The `products` database table (`id`, `name`, `category`, `price`, `stock`, `description`) lacks an authoritative `manufacturer` or `brand` column.
*   **Policy**: The agent **MUST NOT** hallucinate or heuristically extract a manufacturer name from `product_name` unless the user explicitly confirms the brand. 
*   **Safe Behavior**: When the manufacturer is unknown or unverified, the Web Search step must be skipped. The agent will respond with generic advice: "Please refer to the manufacturer documentation provided with your product for official support."

## 5. Web Search Boundary
When an active warranty is confirmed and the brand is explicitly known:
*   **Constraint**: The search query is heavily constrained (e.g., `"{Brand} official warranty customer service"`). Generic web fallback is forbidden.
*   **Validation**: The LLM must verify that the retrieved search results point to an official domain (e.g., matching the brand name) before presenting a URL.
*   **No Claim Submission**: The agent must explicitly state that it is *not* filing a warranty claim on the user's behalf.
*   **Fallback**: If no trusted URL is found in the search abstracts, the agent abandons the search and provides the safe fallback behavior.

## 6. Multi-Turn Workflow State
The skill requires the following conversation state:
*   `customer_id` (injected via session middleware).
*   `order_id` / `product_name` (extracted across conversational turns).
*   `prior_result` (if the LLM previously asked for an `order_id` to disambiguate).
The existing `ProductDomainContext` provides `semantic_intent`, `customer_id`, and `prior_result`, which is sufficient for basic multi-turn state machine logic, provided the LLM is allowed to yield back to the user without calling a tool.

## 7. Ambiguity Resolution Matrix
*   **Multiple Matching Purchases**: Tool returns `AMBIGUITY`. Agent politely asks the user to provide their exact Order ID.
*   **Missing Order/Product**: Tool returns `INVALID_REQUEST`. Agent asks the user what product they are asking about.
*   **Expired Warranty**: Agent informs the user the warranty expired on the deterministic `warranty_expiry` date.
*   **Active Warranty**: Agent confirms the active status and initiates the Web Search boundary (if manufacturer is known).
*   **Missing Warranty Metadata**: Tool returns `MISSING_DATA`. Agent informs the user that warranty data is unavailable and to check their product documentation.
*   **DB Failure**: Tool returns `DB_ERROR`. Agent apologizes for the system issue and asks the user to try again later.
*   **Manufacturer/URL Unknown**: Agent provides the safe generic fallback message.

## 8. Security Guarantees
*   **Database Isolation**: `check_warranty_status` enforces Row Level Security semantics in code by filtering explicitly on `eq("customer_id", customer_id)`.
*   **Python-Layer Validation**: The tool explicitly verifies `order["customer_id"] == customer_id` before evaluating the warranty. Cross-customer data leakage returns a safe `NOT_FOUND`.
*   **Skill Restrictions**: The markdown policy explicitly forbids state-mutating tools. The Python `SkillRuntime` blocks `cancel_order` or `create_ticket` from being executed even if the LLM hallucinates a request.

## 9. Implementation Plan for E.2.1
1.  **Divy (Architecture/Runtime)**:
    *   Upgrade `ProductAgent` to support dynamic LLM tool calling (removing the hardcoded `allowed_tools[0]` pre-execution hack).
    *   Implement the declarative `skills/product/warranty_skill/SKILL.md`.
    *   Register `WarrantySkill` in the `ProductSkillResolver`.
    *   Integrate `web_search` as a read-only tool in the runtime.
2.  **Madan (Tests)**:
    *   Write E2E tests for `WarrantySkill` execution through `ProductAgent`.
    *   Write multi-turn tests (Ambiguity -> User provides Order ID -> Success).
    *   Write Web Search fallback tests.
3.  **Vrishab (Database)**:
    *   No schema changes are required for E.2.1. Manufacturer extraction remains conversational/heuristic for now.
