# Agent to Skill to Tool Matrix

This matrix maps target Domain Agents to their operational Skills and the Shared Tools they are authorized to use.

| Domain Agent | Target Skill | Shared Tools Authorized | Tool Classification |
| --- | --- | --- | --- |
| **GeneralAgent** | `GeneralSupportSkill` | `retrieve_kb`, `web_search` (gated) | Read-Only |
| **ProductAgent** | `ProductInquirySkill` | `list_all_products`, `check_inventory`, `retrieve_kb` | Read-Only |
| **ProductAgent** | `WarrantyWorkflowSkill` | `lookup_customer`, `get_customer_history`, `web_search` (for official service centers) | Read-Only |
| **OrderAgent** | `OrderTrackingSkill` | `track_order`, `lookup_customer` | Read-Only |
| **OrderAgent** | `OrderCancellationSkill` | `track_order`, `cancel_order` | Read/Write (Requires Approval) |
| **OrderAgent** | `OrderIssueSkill` (damaged/missing) | `track_order`, `create_ticket` | Read/Write |
| **PaymentAgent** | `RefundProcessingSkill` | `process_refund`, `track_order` | Read/Write (Requires Approval) |
| **PaymentAgent** | `BillingInquirySkill` | `lookup_customer`, `get_customer_history` | Read-Only |
| **EscalationAgent** | `TicketEscalationSkill` | `get_ticket`, `create_ticket`, `update_ticket`, `get_customer_history`, `send_ticket_email_to_customer` | Read/Write |

## Tool Refactoring Notes
- Existing tools in `tools.py` such as `track_order`, `cancel_order`, `process_refund`, `create_ticket`, `check_inventory` remain unchanged but their invocation moves from a monolithic `db_agent` to these specific Domain Agent Skills.
- `web_search` will be heavily gated. It should only be used by `ProductAgent` during Warranty workflows to find official brand service centers, and by `GeneralAgent` for verified external links.
- `retrieve_kb` (formerly embedded in `rag_agent.py`) will be exposed as a generic tool that any Agent can call if its Skill allows.
