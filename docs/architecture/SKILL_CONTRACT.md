# Skill Contract

## SKILL.md Format
Skills are defined in Markdown files with a YAML frontmatter block containing strongly-typed metadata.

```yaml
---
name: "Skill Name"
version: "1.0.0"
domain: "domain_name"
purpose: "A brief description"
workflow:
  trigger_conditions:
    - "When X happens"
  required_inputs:
    - "query"
  decision_rules:
    - "Rule 1"
  expected_output: "Desired result"
  failure_behavior: "What to do if it fails"
policy:
  allowed_tools:
    - "tool_a"
  forbidden_tools:
    - "tool_b"
  risk_level: "read_only"
  requires_confirmation: false
---

# Instructions
...
```

## Typed Metadata
The frontmatter is parsed into `SkillDefinition`, consisting of three nested sub-models:
1. `SkillMetadata` (Identity and intent)
2. `SkillWorkflow` (Trigger, inputs, rules, failure behavior)
3. `SkillPolicy` (Allowed/Forbidden tools, risk taxonomy)

## Versioning
Versions must follow semantic versioning. The `SkillRuntime` logs the explicit version executed. Duplicate names are rejected at startup.
