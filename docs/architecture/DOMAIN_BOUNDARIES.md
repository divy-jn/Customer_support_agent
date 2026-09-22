# Domain Boundaries

## Current State vs Target State
Currently, intents are routed to agents based on the underlying technology needed to resolve them:
- `db_agent`: handles `billing`, `refund`, `order_tracking`, `order_cancellation`, `account_management`.
- `rag_agent`: handles `faq`, `technical_support`, `product_inquiry`, `general`.
- `web_agent`: acts as a fallback for external info.
- `escalation_agent`: handles complaints.

This forces `db_agent` to be a monolithic expert on multiple distinct business domains (Orders vs Payments).

## Target Domain Ownership

### 1. General Domain
- **Scope**: Store policies, operating hours, general contact info, FAQ.
- **Overlap**: If a user asks a general question about a specific product ("What is your return policy for phones?"), it routes to **ProductDomain** if product specifics apply, or **GeneralDomain** if it's a blanket policy.
- **Routing Rules**: Triggered when no specific entity (order, product, payment) is the subject of the request.

### 2. Product Domain
- **Scope**: Product features, availability, technical support for purchased items, warranty validation.
- **Overlap (Order)**: "The phone I received is damaged." -> This is an **Order** issue initially (fulfillment error/RMA) rather than a pure product inquiry. Once returned/refunded, if they just want it repaired, it shifts to **Product** (Warranty).
- **Decision Rule**: Issues regarding *fulfillment state* (missing, damaged in transit, wrong item) belong to **Order**. Issues regarding *device functionality* or *specifications* belong to **Product**.

### 3. Order Domain
- **Scope**: Order tracking, shipping delays, cancellations, addressing fulfillment errors.
- **Overlap (Payment)**: "I want to cancel my order and get my money back." -> **Order** handles the cancellation logic; the refund is an automated consequence or handed off to **Payment**.
- **Decision Rule**: Anything that mutates or queries the `orders` table lifecycle belongs here.

### 4. Payment Domain
- **Scope**: Billing issues, duplicate deductions, refund status tracking, invoice requests.
- **Overlap (Order)**: See above.
- **Decision Rule**: Anything concerning transactions, money movement, or payment gateway failures belongs here.

### 5. Escalation / Guardian Domain
- **Scope**: Human handoff requests, high-severity complaints, duplicate ticket mitigation, unresolved loops.
- **Decision Rule**: Intervenes when sentiment is strictly negative and urgency is high, or when a user explicitly requests human assistance.
