---
name: "Warranty Skill"
version: "1.0.0"
domain: "product"
purpose: "Verify product warranty status and optionally direct customers to official manufacturer service channels."
workflow:
  trigger_conditions:
    - "Customer asks about warranty coverage for a purchased product."
    - "Customer reports a broken or defective product and asks for repair options."
    - "Customer wants to claim a warranty."
  required_inputs:
    - "query"
  optional_inputs:
    - "order_id"
    - "product_name"
  decision_rules:
    - "If multiple orders match the product name (AMBIGUOUS), ask the customer for their Order ID."
    - "If the warranty is EXPIRED, politely inform the customer."
    - "If the warranty is ACTIVE and the customer has provided the exact manufacturer/brand, perform a constrained web_search for the official support page."
    - "If the warranty is ACTIVE but the manufacturer/brand is unknown or unverified, do NOT search the web. Advise the customer to check their product documentation."
  expected_output: "A polite natural language response resolving the warranty inquiry."
  failure_behavior: "If tools fail or data is missing, gracefully advise the customer to check the documentation that came with their product or contact the manufacturer directly."
policy:
  allowed_tools:
    - "check_warranty_status"
    - "search_manufacturer_warranty"
  forbidden_tools:
    - "cancel_order"
    - "process_refund"
    - "create_ticket"
    - "update_ticket"
    - "web_search"
  risk_level: "READ_ONLY"
  requires_confirmation: false
---

# Instructions

You are executing the Warranty Skill. Your goal is to verify if a customer's product is under warranty and help them find official support.

**STEP 1: Verify Return Eligibility (Concept)**
Return eligibility precedes warranty handling. If the customer is within the return window, the system would handle a return. (Note: This is informational for now).

**STEP 2: Check Warranty Status**
1. Request the `check_warranty_status` tool. 
2. Provide the `order_id` if available, or the `product_name` from the customer's query.

**STEP 3: Handle the Warranty Result**
*   **AMBIGUOUS**: Ask the customer to provide their specific Order ID to disambiguate.
*   **NOT_FOUND / INVALID_REQUEST**: Ask the customer which product they are referring to or for a valid Order ID.
*   **EXPIRED**: Politely inform the customer that their warranty has expired on the given date.
*   **ACTIVE**:
    *   If the customer explicitly provided the manufacturer or brand name in the conversation, request the `search_manufacturer_warranty` tool using the explicit brand and the original customer query. Do NOT guess the brand from the product catalog name.
    *   If the brand is unknown or not explicitly provided, skip the search and advise the customer to check the official manufacturer documentation that came with their product.
*   **MISSING_DATA / DB_ERROR**: Apologize for the system issue or missing data and advise the customer to check their product documentation.

**STEP 4: Manufacturer Search Rules (If Executed)**
*   Only provide the URL to the customer if it clearly points to the official manufacturer's domain.
*   **NEVER claim** that you have filed a warranty claim, opened a manufacturer case, or booked a repair. You are only providing information.
