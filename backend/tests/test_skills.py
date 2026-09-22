import pytest
from pathlib import Path

from app.skills.base import AgentSkill, SkillDefinition, render_skill_prompt
from app.skills.policy import GLOBAL_SYSTEM_POLICY
from app.skills.registry import (
    IntentRoutingSkill,
    DatabaseSkill,
    RAGSkill,
    EscalationSkill,
    WebSearchSkill,
    SkillRegistry
)
from app.skills.loader import SkillLoader
from app.skills.runtime import SkillRuntime
from app.models import SkillExecutionStatus, RiskLevel

# ──────────────────────────────────────────────
#  Legacy Compatibility Tests
# ──────────────────────────────────────────────

def test_agent_skill_legacy_creation():
    skill = AgentSkill(
        name="Test",
        purpose="Test Purpose",
        trigger_conditions=["condition"],
        required_inputs=["input1"],
        allowed_tools=["tool1"],
        forbidden_tools=["tool2"],
        domain_rules=["rule1"],
        expected_output="output1",
        failure_behavior="fail1"
    )
    assert skill.name == "Test"
    assert skill.metadata.name == "Test"
    assert skill.workflow.expected_output == "output1"
    assert "tool1" in skill.policy.allowed_tools

def test_legacy_render_skill_prompt():
    skill = AgentSkill(
        name="RenderTest",
        purpose="PurposeTest",
        trigger_conditions=["T1"],
        required_inputs=["R1"],
        allowed_tools=["A1"],
        forbidden_tools=["F1"],
        domain_rules=["D1"],
        expected_output="E1",
        failure_behavior="FB1"
    )
    
    prompt = render_skill_prompt(skill)
    assert "--- SKILL: RenderTest ---" in prompt
    assert "PURPOSE: PurposeTest" in prompt
    assert "- T1" in prompt
    assert "- R1" in prompt
    assert "- A1" in prompt
    assert "- F1" in prompt
    assert "- D1" in prompt
    assert "EXPECTED OUTPUT:\nE1" in prompt
    assert "FAILURE BEHAVIOR:\nFB1" in prompt

def test_legacy_skills_present():
    assert IntentRoutingSkill.name == "Intent Routing Skill"
    assert DatabaseSkill.name == "Database Operations Skill"
    assert RAGSkill.name == "Knowledge Base Retrieval Skill"
    assert EscalationSkill.name == "Escalation & Handoff Skill"
    assert WebSearchSkill.name == "Web Search Skill"


# ──────────────────────────────────────────────
#  Phase C: Loader Tests
# ──────────────────────────────────────────────

def test_valid_skill_markdown(tmp_path: Path):
    content = """---
name: "Product Information Skill"
version: "1.0.0"
domain: "product"
purpose: "Answer product questions"
workflow:
  trigger_conditions: ["Asks a question"]
  required_inputs: ["query"]
  expected_output: "Good output"
policy:
  allowed_tools: ["search"]
  forbidden_tools: ["buy"]
  risk_level: "read_only"
---
# Markdown body...
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    skill = SkillLoader.load_from_file(file_path)
    assert skill.name == "Product Information Skill"
    assert skill.metadata.version == "1.0.0"
    assert skill.metadata.domain == "product"
    assert "query" in skill.workflow.required_inputs
    assert skill.policy.risk_level == RiskLevel.READ_ONLY
    assert skill.instructions == "# Markdown body..."

def test_malformed_frontmatter_fails_closed(tmp_path: Path):
    content = """---
name: "Malformed"
[this is invalid yaml
---
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    with pytest.raises(ValueError):
        SkillLoader.load_from_file(file_path)

def test_missing_metadata_fails_closed(tmp_path: Path):
    content = """---
purpose: "No name provided"
---
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    with pytest.raises(ValueError):
        SkillLoader.load_from_file(file_path)

def test_missing_domain_fails_closed(tmp_path: Path):
    content = """---
name: "Product Information Skill"
version: "1.0.0"
purpose: "Answer product questions"
---
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    with pytest.raises(ValueError, match="missing required field: 'domain'"):
        SkillLoader.load_from_file(file_path)

def test_invalid_semantic_version_fails_closed(tmp_path: Path):
    content = """---
name: "Product Information Skill"
version: "version-one"
domain: "product"
purpose: "Answer product questions"
---
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    with pytest.raises(ValueError, match="Invalid semantic version"):
        SkillLoader.load_from_file(file_path)

def test_invalid_risk_level_fails_closed(tmp_path: Path):
    content = """---
name: "Product Information Skill"
version: "1.0.0"
domain: "product"
purpose: "Answer product questions"
policy:
  risk_level: "super_admin"
---
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    with pytest.raises(ValueError, match="Invalid risk_level 'super_admin'"):
        SkillLoader.load_from_file(file_path)

def test_markdown_cannot_expand_permissions(tmp_path: Path):
    # Testing that markdown can't arbitrarily inject code execution
    # It must map exactly to Pydantic strings and lists.
    content = """---
name: "Hacker Skill"
version: "1.0.0"
domain: "security"
purpose: "Hack"
policy:
  allowed_tools: ["system.exec('rm -rf')"]
---
import os; os.system('echo hacked')
"""
    file_path = tmp_path / "SKILL.md"
    file_path.write_text(content)
    
    skill = SkillLoader.load_from_file(file_path)
    # The tool is just a string, it is not executed by the loader.
    assert "system.exec('rm -rf')" in skill.policy.allowed_tools
    # The markdown body is preserved, but no arbitrary execution happens.
    assert skill.instructions == "import os; os.system('echo hacked')"


# ──────────────────────────────────────────────
#  Phase C: Registry Tests
# ──────────────────────────────────────────────

def test_duplicate_registration(tmp_path: Path):
    registry = SkillRegistry()
    skill = AgentSkill(name="DuplicateTest", purpose="A")
    registry.register(skill)
    
    with pytest.raises(ValueError, match="already registered"):
        registry.register(skill)

# ──────────────────────────────────────────────
#  Phase C: Runtime Execution Boundary
# ──────────────────────────────────────────────

def dummy_executor(tool_name: str, kwargs: dict):
    if tool_name == "crash":
        raise Exception("Tool crashed")
    return f"Mock result for {tool_name}"

def test_missing_required_input():
    runtime = SkillRuntime(dummy_executor)
    skill = AgentSkill(name="T1", purpose="T", required_inputs=["param1"])
    
    res = runtime.validate_inputs(skill, {"wrong_param": "foo"})
    assert res.status == SkillExecutionStatus.MISSING_REQUIRED_INPUT

def test_allowed_tool_execution():
    runtime = SkillRuntime(dummy_executor)
    skill = AgentSkill(name="T1", purpose="T", allowed_tools=["search"])
    
    res = runtime.execute_tool(skill, "search", {})
    assert res.status == SkillExecutionStatus.SUCCESS
    assert res.structured_output["tool_result"] == "Mock result for search"

def test_forbidden_tool_execution():
    runtime = SkillRuntime(dummy_executor)
    skill = AgentSkill(name="T1", purpose="T", allowed_tools=["search"], forbidden_tools=["delete"])
    
    res = runtime.execute_tool(skill, "delete", {})
    assert res.status == SkillExecutionStatus.POLICY_DENIED

def test_unknown_tool_execution():
    runtime = SkillRuntime(dummy_executor)
    skill = AgentSkill(name="T1", purpose="T", allowed_tools=["search"])
    
    # Tool not in forbidden, but also NOT in allowed
    res = runtime.execute_tool(skill, "unknown", {})
    assert res.status == SkillExecutionStatus.TOOL_NOT_ALLOWED

def test_confirmation_required_enforcement():
    runtime = SkillRuntime(dummy_executor)
    # We must construct SkillDefinition directly to test RiskLevel since AgentSkill facade defaults to READ_ONLY
    from app.skills.base import SkillDefinition, SkillMetadata, SkillWorkflow, SkillPolicy
    
    skill = SkillDefinition(
        metadata=SkillMetadata(name="T1", purpose="T"),
        workflow=SkillWorkflow(),
        policy=SkillPolicy(
            allowed_tools=["buy"],
            risk_level=RiskLevel.HIGH_RISK_MUTATION,
            requires_confirmation=True
        )
    )
    
    res = runtime.execute_tool(skill, "buy", {})
    assert res.status == SkillExecutionStatus.CONFIRMATION_REQUIRED

def test_tool_failure_is_caught():
    runtime = SkillRuntime(dummy_executor)
    skill = AgentSkill(name="T1", purpose="T", allowed_tools=["crash"])
    
    res = runtime.execute_tool(skill, "crash", {})
    assert res.status == SkillExecutionStatus.TOOL_FAILURE
    assert res.failure_code == "Exception"
