from pydantic import BaseModel, Field
from typing import List, Optional
from app.models import RiskLevel

class SkillMetadata(BaseModel):
    name: str
    version: str = "1.0.0"
    domain: str = "general"
    purpose: str

class SkillWorkflow(BaseModel):
    trigger_conditions: List[str] = Field(default_factory=list)
    required_inputs: List[str] = Field(default_factory=list)
    optional_inputs: List[str] = Field(default_factory=list)
    decision_rules: List[str] = Field(default_factory=list)
    expected_output: str = ""
    failure_behavior: str = ""

class SkillPolicy(BaseModel):
    allowed_tools: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    requires_confirmation: bool = False

class SkillDefinition(BaseModel):
    metadata: SkillMetadata
    workflow: SkillWorkflow
    policy: SkillPolicy
    
    @property
    def name(self) -> str:
        return self.metadata.name
        
    @property
    def purpose(self) -> str:
        return self.metadata.purpose
        
    @property
    def allowed_tools(self) -> List[str]:
        return self.policy.allowed_tools
        
    @property
    def forbidden_tools(self) -> List[str]:
        return self.policy.forbidden_tools


# ──────────────────────────────────────────────
#  Legacy Compatibility Facade
# ──────────────────────────────────────────────
class AgentSkill(SkillDefinition):
    """
    Legacy compatibility facade for existing agents.
    It constructs the new strongly-typed nested models transparently.
    """
    def __init__(self, **kwargs):
        # Translate flat legacy kwargs into nested structures
        metadata = SkillMetadata(
            name=kwargs.get("name", ""),
            purpose=kwargs.get("purpose", "")
        )
        workflow = SkillWorkflow(
            trigger_conditions=kwargs.get("trigger_conditions", []),
            required_inputs=kwargs.get("required_inputs", []),
            decision_rules=kwargs.get("domain_rules", []),
            expected_output=kwargs.get("expected_output", ""),
            failure_behavior=kwargs.get("failure_behavior", "")
        )
        policy = SkillPolicy(
            allowed_tools=kwargs.get("allowed_tools", []),
            forbidden_tools=kwargs.get("forbidden_tools", [])
        )
        super().__init__(metadata=metadata, workflow=workflow, policy=policy)


def render_skill_prompt(skill: SkillDefinition) -> str:
    """Renders the SkillDefinition into a prompt format for LLM context."""
    prompt = f"--- SKILL: {skill.metadata.name} ---\n"
    prompt += f"PURPOSE: {skill.metadata.purpose}\n\n"
    
    if skill.workflow.trigger_conditions:
        prompt += "TRIGGER CONDITIONS:\n"
        for cond in skill.workflow.trigger_conditions:
            prompt += f"- {cond}\n"
        prompt += "\n"
        
    if skill.workflow.required_inputs:
        prompt += "REQUIRED INPUTS:\n"
        for req in skill.workflow.required_inputs:
            prompt += f"- {req}\n"
        prompt += "\n"
        
    if skill.policy.allowed_tools:
        prompt += "ALLOWED TOOLS:\n"
        for tool in skill.policy.allowed_tools:
            prompt += f"- {tool}\n"
        prompt += "\n"
        
    if skill.policy.forbidden_tools:
        prompt += "FORBIDDEN TOOLS:\n"
        for tool in skill.policy.forbidden_tools:
            prompt += f"- {tool}\n"
        prompt += "\n"
        
    if skill.workflow.decision_rules:
        prompt += "DOMAIN RULES:\n"
        for rule in skill.workflow.decision_rules:
            prompt += f"- {rule}\n"
        prompt += "\n"
        
    prompt += f"EXPECTED OUTPUT:\n{skill.workflow.expected_output}\n\n"
    prompt += f"FAILURE BEHAVIOR:\n{skill.workflow.failure_behavior}\n"
    
    return prompt
