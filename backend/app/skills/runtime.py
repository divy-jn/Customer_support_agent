from typing import Callable, Any, Dict, Protocol
from app.skills.base import SkillDefinition
from app.models import SkillExecutionResult, SkillExecutionStatus, RiskLevel

class ToolExecutor(Protocol):
    def __call__(self, tool_name: str, kwargs: Dict[str, Any]) -> Any:
        ...

class SkillRuntime:
    """
    The secure boundary for executing tools on behalf of a skill.
    Responsible for policy enforcement, risk checks, and yielding structured results.
    """
    def __init__(self, tool_executor: ToolExecutor):
        self.tool_executor = tool_executor
        
    def validate_inputs(self, skill: SkillDefinition, inputs: Dict[str, Any]) -> SkillExecutionResult:
        """Validate that all required inputs declared by the skill are present."""
        missing = [req for req in skill.workflow.required_inputs if req not in inputs]
        
        if missing:
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.MISSING_REQUIRED_INPUT,
                message=f"Missing required inputs: {missing}"
            )
            
        return SkillExecutionResult(
            skill_name=skill.name,
            skill_version=skill.metadata.version,
            status=SkillExecutionStatus.SUCCESS,
            message="Inputs validated successfully."
        )
        
    def execute_tool(self, skill: SkillDefinition, tool_name: str, kwargs: Dict[str, Any]) -> SkillExecutionResult:
        """
        Execute a tool safely within the constraints of the skill's policy.
        """
        # 1. Enforce strict forbiddance (Wins over allowed)
        if tool_name in skill.policy.forbidden_tools:
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.POLICY_DENIED,
                message=f"Tool '{tool_name}' is explicitly forbidden by this skill policy."
            )
            
        # 2. Enforce explicit allowed list
        if tool_name not in skill.policy.allowed_tools:
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.TOOL_NOT_ALLOWED,
                message=f"Tool '{tool_name}' is not authorized in the allowed list for this skill."
            )
            
        # 3. Enforce Confirmation logic based on Risk Policy
        # Even if allowed, if it's high risk and the skill policy demands confirmation, we halt.
        if skill.policy.requires_confirmation and skill.policy.risk_level in (RiskLevel.HIGH_RISK_MUTATION, RiskLevel.EXTERNAL_SIDE_EFFECT):
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.CONFIRMATION_REQUIRED,
                message=f"Execution of '{tool_name}' requires explicit confirmation due to risk level."
            )
            
        # 4. Actual execution via decoupled executor
        try:
            result = self.tool_executor(tool_name, kwargs)
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.SUCCESS,
                structured_output={"tool_result": result}
            )
        except Exception as e:
            return SkillExecutionResult(
                skill_name=skill.name,
                skill_version=skill.metadata.version,
                status=SkillExecutionStatus.TOOL_FAILURE,
                message=f"Tool '{tool_name}' encountered an error during execution.",
                failure_code=str(e)
            )
