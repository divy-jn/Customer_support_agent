from typing import Dict, List, Optional
from app.skills.base import AgentSkill, SkillDefinition
from app.skills.loader import SkillLoader
from pathlib import Path

class SkillRegistry:
    """
    Registry for loading and resolving domain skills.
    Ensures that only valid, unique skills are registered.
    """
    def __init__(self):
        self._skills: Dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        """Register a validated skill. Prevents duplicates."""
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' is already registered.")
        self._skills[skill.name] = skill
        
    def load_from_directory(self, directory: str | Path) -> None:
        """Dynamically load all SKILL.md files from a directory tree."""
        path = Path(directory)
        if not path.exists() or not path.is_dir():
            return
            
        for md_file in path.rglob("SKILL.md"):
            try:
                skill = SkillLoader.load_from_file(md_file)
                self.register(skill)
            except Exception as e:
                # Log or raise? The prompt says "fail closed".
                # If one is malformed, we probably want to raise and halt startup,
                # or just log it and refuse to register. Let's raise to fail fast on startup.
                raise ValueError(f"Failed to load skill from {md_file}: {e}")
                
    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """Resolve a skill by stable name."""
        return self._skills.get(name)
        
    def list_skills(self, domain: Optional[str] = None) -> List[SkillDefinition]:
        """List all skills, optionally filtered by domain."""
        if domain:
            return [s for s in self._skills.values() if s.metadata.domain == domain]
        return list(self._skills.values())

# Global registry instance for the new system
registry = SkillRegistry()


# ──────────────────────────────────────────────
#  Legacy Skills (Preserved for compatibility)
# ──────────────────────────────────────────────

IntentRoutingSkill = AgentSkill(
    name="Intent Routing Skill",
    purpose="Accurately classify customer intent, sentiment, and urgency to route them to the appropriate domain agent.",
    trigger_conditions=[
        "Every incoming customer message before it is processed by domain agents."
    ],
    required_inputs=[
        "Customer message text",
        "Recent conversation context"
    ],
    allowed_tools=[],
    forbidden_tools=[],
    domain_rules=[
        "Route to 'rag_agent' for: faq, technical_support, product_inquiry, general questions about policies/products.",
        "Route to 'db_agent' for: billing, refund, order_tracking, order_cancellation, account_management (anything needing database lookup).",
        "Route to 'web_agent' for: questions about things outside our knowledge base (competitor products, general tech questions).",
        "Route to 'escalation' for: when sentiment is 'negative' AND urgency is 'high' or 'critical', OR when the customer explicitly asks for a human agent.",
        "SEMANTIC CLASSIFICATION EXAMPLES:",
        "- 'Where is my package?' -> intent: 'order_tracking'",
        "- 'Has my shipment arrived yet?' -> intent: 'order_tracking'",
        "- 'I need my money back for this order.' -> intent: 'refund'",
        "- 'I want to stop the order I placed.' -> intent: 'order_cancellation'",
        "- 'I am extremely frustrated and need a manager.' -> intent: 'complaint' (route_to: 'escalation')",
        "- 'Yes, do it.' (in the context of an agent asking about a refund) -> intent: 'refund'"
    ],
    expected_output="A valid JSON object containing exactly these fields: intent (str), sentiment (str), urgency (str), route_to (str), reasoning (str).",
    failure_behavior="If classification fails, default to 'general' intent, 'neutral' sentiment, 'medium' urgency, and route to 'rag_agent'."
)

DatabaseSkill = AgentSkill(
    name="Database Operations Skill",
    purpose="Provide personalized customer support by querying the database for orders, tickets, and accounts, and taking action on them.",
    trigger_conditions=[
        "Customer asks about order status, refunds, or cancellations.",
        "Customer asks to manage or check their tickets."
    ],
    required_inputs=[
        "TOOL RESULTS from the database."
    ],
    allowed_tools=[
        "lookup_customer", "get_customer_history", "get_ticket", "create_ticket",
        "update_ticket", "track_order", "cancel_order", "process_refund",
        "check_inventory", "list_all_products", "get_chat_history", "send_ticket_email_to_customer"
    ],
    forbidden_tools=[
        "web_search", "retrieve_as_context"
    ],
    domain_rules=[
        "Always reference specific data from the tool results (order numbers, ticket IDs, status, etc.).",
        "If an order ID is required for a refund or cancellation but not provided in the tool results or context, clearly ask the customer for it.",
        "If a tool returned an error, explain the situation clearly to the customer.",
        "Suggest next steps when appropriate."
    ],
    expected_output="A polite, helpful, and natural language response answering the customer's query using only the provided tool results.",
    failure_behavior="If unable to process the database request, apologize and offer to connect them with a human agent."
)

RAGSkill = AgentSkill(
    name="Knowledge Base Retrieval Skill",
    purpose="Answer customer questions accurately using the company's internal knowledge base.",
    trigger_conditions=[
        "Customer asks a general policy question.",
        "Customer asks for technical support or product information."
    ],
    required_inputs=[
        "Relevant retrieved context snippets from the knowledge base."
    ],
    allowed_tools=[
        "retrieve_as_context"
    ],
    forbidden_tools=[
        "database mutation tools (create_ticket, process_refund, cancel_order)"
    ],
    domain_rules=[
        "ONLY answer based on the provided CONTEXT. Do NOT use any external knowledge.",
        "When referencing policies (return window, warranty period, etc.), quote the exact numbers from the context.",
        "CRITICAL: If you use information from the CONTEXT, you MUST cite the source document name naturally in your response or at the end. For example: 'According to our Return Policy...' or 'Source: return_policy.md'.",
        "Do NOT fabricate source names; only use the exact names provided in the CONTEXT."
    ],
    expected_output="A concise, friendly, and professional response strictly grounded in the provided context, complete with source citations.",
    failure_behavior="If the CONTEXT does not contain the answer, say: 'I don't have specific information about that in our knowledge base. Let me connect you with a human agent who can help.'"
)

EscalationSkill = AgentSkill(
    name="Escalation & Handoff Skill",
    purpose="De-escalate frustrated customers and smoothly transition the conversation to a human support agent.",
    trigger_conditions=[
        "Customer is angry, frustrated, or urgently demanding help.",
        "Customer explicitly asks to speak to a human or live agent."
    ],
    required_inputs=[
        "Customer's message context indicating frustration or handoff."
    ],
    allowed_tools=[],
    forbidden_tools=[],
    domain_rules=[
        "Acknowledge the customer's frustration empathetically.",
        "Assure them that a human agent is being notified immediately.",
        "Do not try to solve their underlying technical or billing problem yourself at this stage."
    ],
    expected_output="A relatively short, polite, and empathetic handover message.",
    failure_behavior="If failing to generate a custom response, fallback to a standard hardcoded 'I am transferring you to a human agent right now' message."
)

WebSearchSkill = AgentSkill(
    name="Web Search Skill",
    purpose="Answer questions that fall outside the company's internal knowledge base by searching the public internet.",
    trigger_conditions=[
        "Customer asks about competitor products or general tech questions not covered by internal docs."
    ],
    required_inputs=[
        "TOOL RESULTS from the web search."
    ],
    allowed_tools=[
        "web_search"
    ],
    forbidden_tools=[
        "retrieve_as_context", "database tools"
    ],
    domain_rules=[
        "Provide a helpful answer based ONLY on the web search results.",
        "Do not invent information. If the search results don't contain the answer, apologize and state you couldn't find the information."
    ],
    expected_output="A concise and professional response based on the web search results.",
    failure_behavior="If web search fails or yields no relevant info, apologize and state you cannot access the requested information."
)
