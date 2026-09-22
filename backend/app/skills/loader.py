import os
import yaml
from pathlib import Path
from pydantic import ValidationError
from typing import Optional

from app.skills.base import SkillDefinition, SkillMetadata, SkillWorkflow, SkillPolicy
from app.models import RiskLevel

class SkillLoader:
    @staticmethod
    def load_from_file(file_path: str | Path) -> SkillDefinition:
        """
        Loads a skill definition from a Markdown file with YAML frontmatter.
        Fails safely and explicitly on any malformed input.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Skill file not found: {file_path}")
            
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise ValueError(f"Failed to read skill file {file_path}: {e}")
            
        # Parse YAML frontmatter
        if not content.startswith("---"):
            raise ValueError(f"Skill file {file_path} is missing YAML frontmatter (must start with '---')")
            
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Skill file {file_path} has malformed YAML frontmatter (missing closing '---')")
            
        yaml_content = parts[1].strip()
        markdown_body = parts[2].strip()
        
        if not yaml_content:
            raise ValueError(f"Skill file {file_path} has empty YAML frontmatter")
            
        try:
            parsed = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML frontmatter in {file_path}: {e}")
            
        if not isinstance(parsed, dict):
            raise ValueError(f"YAML frontmatter in {file_path} must be a dictionary")
            
        # Extract fields to match our nested structure
        try:
            metadata = SkillMetadata(
                name=parsed.get("name"),
                version=str(parsed.get("version", "1.0.0")),
                domain=parsed.get("domain"),
                purpose=parsed.get("purpose")
            )
            
            # workflow config might be nested or flat
            wf_config = parsed.get("workflow", parsed)
            workflow = SkillWorkflow(
                trigger_conditions=wf_config.get("trigger_conditions", []),
                required_inputs=wf_config.get("required_inputs", []),
                optional_inputs=wf_config.get("optional_inputs", []),
                decision_rules=wf_config.get("decision_rules", []),
                expected_output=wf_config.get("expected_output", ""),
                failure_behavior=wf_config.get("failure_behavior", "")
            )
            
            # policy config might be nested or flat
            pol_config = parsed.get("policy", parsed)
            
            risk_val = pol_config.get("risk_level", "read_only")
            try:
                risk_level = RiskLevel(risk_val)
            except ValueError:
                risk_level = RiskLevel.READ_ONLY
                
            policy = SkillPolicy(
                allowed_tools=pol_config.get("allowed_tools", []),
                forbidden_tools=pol_config.get("forbidden_tools", []),
                risk_level=risk_level,
                requires_confirmation=pol_config.get("requires_confirmation", False)
            )
            
            return SkillDefinition(metadata=metadata, workflow=workflow, policy=policy)
            
        except ValidationError as e:
            raise ValueError(f"Validation failed for skill {file_path}: {e}")
