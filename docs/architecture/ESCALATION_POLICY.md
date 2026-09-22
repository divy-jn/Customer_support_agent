# Guardian / Escalation Policy

## Philosophy
Escalation is not merely "user asks for a manager -> create a ticket". It must be a structured policy that considers the customer's history, active tickets, and whether the issue is genuinely new or a duplicate.

## Escalation Decision Flow

When an escalation trigger occurs (Negative sentiment + high urgency, explicit human request, or domain agent failure):

1. **Retrieve Support History**: Call `get_customer_history` to fetch recent orders and tickets.
2. **Identify Active/Recent Tickets**: Check for tickets created in the last 24-48 hours.
3. **Determine Resolution Path**:
   - **Condition A (Duplicate Prevention)**: If there is an *Active* ticket matching the current issue domain/intent.
     - *Action*: DO NOT create a new ticket. Inform the customer that their case (Ticket #X) is already being handled, provide the latest status, and optionally escalate the priority of the *existing* ticket.
   - **Condition B (Agent Capability)**: If the customer asks for a human, but the issue is a simple, authorized action (e.g., canceling a brand new order), the Guardian should allow the Domain Agent to attempt resolution *first* (while acknowledging the human request).
   - **Condition C (Unresolved Loop)**: If the user has made >=3 attempts in the same session without resolution.
     - *Action*: Force escalation and create a new ticket (if no active ticket exists), assigning to high-priority queue.
   - **Condition D (Valid New Escalation)**: No active tickets match, and issue requires human intervention.
     - *Action*: Create a new ticket, notify customer, end automated workflow.

## Rule Definitions

- **When to resolve**: The domain agent possesses the exact capability to satisfy the request (e.g., automated refund for a shipped order is not allowed, but tracking is).
- **When to continue**: The user is frustrated but missing required entities (e.g., "Cancel my order!" -> Agent must still collect the order ID before escalating).
- **When to escalate existing case**: User asks for updates on an issue they complained about yesterday. The agent updates the existing ticket's priority instead of spamming the ticketing system.
- **When to create new ticket**: A genuinely new issue that automation cannot handle.
- **When to refuse/avoid duplicate escalation**: User repeatedly spams "let me talk to someone" for an issue that already has an open, high-priority ticket. Agent politely refuses to create duplicate tickets.
