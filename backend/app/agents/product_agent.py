"""
Product Agent (Phase D.1)

Owns product-domain reasoning. Receives semantic context already classified
as the "product" domain and delegates to the appropriate Product skill
through the SkillRuntime boundary.

Does NOT:
- perform semantic routing
- directly call low-level tools
- mutate transactional state (orders, refunds, tickets)
- bypass SkillRuntime policy enforcement
"""

import time
import logging
from typing import Any, Dict, List, Optional, Protocol
from pydantic import BaseModel

from app.skills.base import SkillDefinition, render_skill_prompt
from app.skills.policy import GLOBAL_SYSTEM_POLICY
from app.skills.registry import SkillRegistry
from app.skills.runtime import SkillRuntime
from app.models import SkillExecutionStatus, SkillExecutionResult


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Product Domain Context (Bounded)
# ──────────────────────────────────────────────

class ProductDomainContext(BaseModel):
    """Bounded context that ProductAgent receives for each turn."""
    customer_message: str
    semantic_intent: str = ""
    entities: Dict[str, Any] = {}
    session_id: str = ""
    customer_id: Optional[int] = None
    prior_result: Optional[str] = None


# ──────────────────────────────────────────────
#  Product Agent Response
# ──────────────────────────────────────────────

class ProductAgentResponse(BaseModel):
    """Structured response produced by ProductAgent."""
    response: str
    domain: str = "product"
    skill_used: str = ""
    skill_version: str = ""
    execution_status: str = ""
    sources: List[str] = []
    metadata: Dict[str, Any] = {}


# ──────────────────────────────────────────────
#  LLM Adapter Protocol
# ──────────────────────────────────────────────

class LLMAdapter(Protocol):
    """Protocol for LLM invocation — decouples ProductAgent from any specific provider."""
    async def invoke(self, system_prompt: str, user_prompt: str) -> str:
        ...


# ──────────────────────────────────────────────
#  Product Skill Resolver
# ──────────────────────────────────────────────

# Explicit intent → skill name mapping.
# Python selects the skill deterministically — the LLM only provides the intent.
# Unknown or out-of-domain intents return None.
_PRODUCT_INTENT_MAP: Dict[str, str] = {
    "product_inquiry": "Product Information Skill",
    "product_information": "Product Information Skill",
    "product_features": "Product Information Skill",
    "product_specs": "Product Information Skill",
    "product_details": "Product Information Skill",
    "product_availability": "Product Information Skill",
    "product_question": "Product Information Skill",
    "technical_support": "Product Information Skill",
    "product_comparison": "Product Information Skill",
    "warranty_claim": "Warranty Skill",
    "product_warranty": "Warranty Skill",
    # Future: "product_troubleshooting": "Product Troubleshooting Skill",
}


class ProductSkillResolver:
    """
    Deterministic skill resolution for the Product domain.

    Delegates actual skill storage to the central SkillRegistry (Phase C).
    Owns only the domain-specific intent → skill-name mapping.
    """

    def __init__(self, registry: SkillRegistry):
        self._registry = registry

    def resolve(self, intent: str) -> Optional[SkillDefinition]:
        """
        Map a semantic intent to a Product-domain skill using explicit mappings only.
        Returns None if no mapping exists or the skill is not registered.
        """
        intent_lower = intent.lower() if intent else ""
        skill_name = _PRODUCT_INTENT_MAP.get(intent_lower)
        if skill_name is None:
            return None
        return self._registry.get_skill(skill_name)

    def list_skills(self) -> List[SkillDefinition]:
        """List all product-domain skills from the central registry."""
        return self._registry.list_skills(domain="product")


# ──────────────────────────────────────────────
#  Product Agent
# ──────────────────────────────────────────────

PRODUCT_AGENT_IDENTITY = """You are the Product Domain Agent.
Your responsibility is to help customers with product-related questions:
features, specifications, availability, comparisons, basic usage, and warranty checking.

You do NOT handle: order tracking, cancellations, refunds, billing,
ticket creation, or escalation. If the customer asks about those,
politely inform them that you specialize in product information and
they will be connected with the appropriate team.

Follow the skill operating instructions below precisely.

TOOL CALLING:
To request a tool, output a JSON block exactly in this format on its own lines:
```json
{
  "tool_call": "<tool_name>",
  "arguments": {
    "<arg_name>": "<arg_value>"
  }
}
```
You must stop your response after outputting a tool request. 
The system will execute the tool and provide you with the result in the next turn.
Once you have enough information to resolve the user's request, provide your final natural language response and DO NOT output any JSON tool call."""


class ProductAgent:
    """
    Domain agent for the Product vertical.

    Responsibilities:
    - Receive product-domain classified requests
    - Select an appropriate Product skill via ProductSkillResolver
    - Supply bounded workflow context
    - Request permitted capabilities through SkillRuntime
    - Synthesize a customer-facing response from verified results

    Does NOT:
    - Perform semantic routing
    - Decide arbitrary domain ownership
    - Bypass SkillRuntime
    - Directly call low-level tools
    - Mutate transactional state
    """

    def __init__(
        self,
        skill_resolver: ProductSkillResolver,
        skill_runtime: SkillRuntime,
        llm_adapter: LLMAdapter,
    ):
        self.skill_resolver = skill_resolver
        self.skill_runtime = skill_runtime
        self.llm_adapter = llm_adapter

    async def handle(self, context: ProductDomainContext) -> ProductAgentResponse:
        """
        Main entry point. Executes the full:
        context → skill resolution → capability execution → response generation
        pipeline.
        """
        start_time = time.time()

        # ── Step 1: Resolve skill ──
        skill = self.skill_resolver.resolve(context.semantic_intent)
        if skill is None:
            logger.warning(
                "ProductAgent: no skill matched for intent=%s",
                context.semantic_intent,
            )
            return ProductAgentResponse(
                response=(
                    "I'm sorry, I don't have the specific expertise to handle "
                    "that product request right now. Let me connect you with "
                    "someone who can help."
                ),
                execution_status="no_skill_matched",
                metadata={
                    "intent": context.semantic_intent,
                    "latency_ms": int((time.time() - start_time) * 1000),
                },
            )

        # ── Step 2: Validate inputs ──
        input_result = self.skill_runtime.validate_inputs(
            skill, {"Customer query text": context.customer_message}
        )
        if input_result.status != SkillExecutionStatus.SUCCESS:
            return ProductAgentResponse(
                response="I need more information to help you with that product question.",
                skill_used=skill.name,
                skill_version=skill.metadata.version,
                execution_status=input_result.status.value,
                metadata={"message": input_result.message},
            )

        # ── Step 3: ReAct Tool Calling Loop ──
        MAX_ITERATIONS = 5
        iteration = 0
        
        system_prompt = self._build_system_prompt(skill)
        user_prompt = self._build_user_prompt(context, "")
        
        # We maintain a conversation transcript for the LLM
        conversation_history = user_prompt
        
        while iteration < MAX_ITERATIONS:
            iteration += 1
            
            try:
                llm_response = await self.llm_adapter.invoke(system_prompt, conversation_history)
            except Exception as e:
                logger.error("ProductAgent: LLM invocation failed: %s", type(e).__name__)
                return ProductAgentResponse(
                    response=(
                        "I apologize, but I'm experiencing a technical issue. "
                        "Let me connect you with a human agent."
                    ),
                    skill_used=skill.name,
                    skill_version=skill.metadata.version,
                    execution_status="llm_failure",
                    metadata={"failure_code": type(e).__name__},
                )
            
            # Parse for tool call JSON
            import json
            import re
            
            tool_call_match = re.search(r"```json\s*(\{.*?\})\s*```", llm_response, re.DOTALL)
            if not tool_call_match:
                # Fallback to look for raw json if no backticks
                tool_call_match = re.search(r"(\{\s*\"tool_call\".*?\})", llm_response, re.DOTALL)
                
            if tool_call_match:
                try:
                    tool_request = json.loads(tool_call_match.group(1))
                    tool_name = tool_request.get("tool_call")
                    arguments = tool_request.get("arguments", {})
                    
                    if not tool_name:
                        raise ValueError("Missing 'tool_call' in JSON")
                        
                    # Execute tool through SkillRuntime Policy Enforcer
                    exec_result = self.skill_runtime.execute_tool(
                        skill,
                        tool_name,
                        arguments,
                    )
                    
                    # Ensure typed results survive the boundary and are serialized ONLY for the LLM context here.
                    tool_result_raw = exec_result.structured_output.get("tool_result")
                    
                    # Dump model to json if it is a Pydantic model (like WarrantyStatusResult)
                    if hasattr(tool_result_raw, "model_dump_json"):
                        tool_result_str = tool_result_raw.model_dump_json()
                    elif isinstance(tool_result_raw, str):
                        tool_result_str = tool_result_raw
                    else:
                        tool_result_str = json.dumps(tool_result_raw, default=str)
                    
                    if exec_result.status != SkillExecutionStatus.SUCCESS:
                        tool_result_str = f"TOOL EXECUTOR ERROR ({exec_result.status.value}): {exec_result.message}"
                        
                    # Append assistant's request and the tool's result to history
                    conversation_history += f"\n\nAssistant requested tool: {tool_name}\nTool Result:\n{tool_result_str}\n\nPlease continue."
                    
                except json.JSONDecodeError:
                    conversation_history += f"\n\nAssistant attempted to call a tool but provided invalid JSON. Please fix the formatting.\n\nPlease continue."
                except Exception as e:
                    conversation_history += f"\n\nAssistant attempted to call a tool but encountered an error: {str(e)}\n\nPlease continue."
            else:
                # No tool call detected, this is the final response.
                latency_ms = int((time.time() - start_time) * 1000)
                return ProductAgentResponse(
                    response=llm_response,
                    skill_used=skill.name,
                    skill_version=skill.metadata.version,
                    execution_status=SkillExecutionStatus.SUCCESS.value,
                    metadata={
                        "domain": "product",
                        "skill_name": skill.name,
                        "skill_version": skill.metadata.version,
                        "latency_ms": latency_ms,
                        "iterations": iteration
                    },
                )

        # ── Step 4: Max iterations exceeded ──
        logger.warning("ProductAgent: Max tool iterations exceeded for skill=%s", skill.name)
        return ProductAgentResponse(
            response="I apologize, but I'm having trouble retrieving the requested information right now. Please try again later.",
            skill_used=skill.name,
            skill_version=skill.metadata.version,
            execution_status="max_iterations_exceeded",
        )

    def _build_system_prompt(self, skill: SkillDefinition) -> str:
        """
        Compose the system prompt from:
        1. Global system policy
        2. ProductAgent domain identity (minimal)
        3. Skill instructions (from SKILL.md markdown body)
        4. Skill workflow rules (from render_skill_prompt)

        The actual workflow instructions come from the resolved SkillDefinition.
        """
        parts = [
            GLOBAL_SYSTEM_POLICY,
            PRODUCT_AGENT_IDENTITY,
        ]
        if skill.instructions:
            parts.append(f"\n--- SKILL INSTRUCTIONS ---\n{skill.instructions}")
        parts.append(render_skill_prompt(skill))
        return "\n\n".join(parts)

    def _build_user_prompt(
        self,
        context: ProductDomainContext,
        retrieved_context: str,
    ) -> str:
        """Build the user prompt with bounded context."""
        parts = []
        if retrieved_context:
            parts.append(f"CONTEXT:\n{retrieved_context}")
        if context.prior_result:
            parts.append(f"Prior result:\n{context.prior_result}")
        parts.append(f"Customer's question: {context.customer_message}")
        return "\n\n".join(parts)
