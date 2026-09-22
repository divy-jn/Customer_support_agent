# Skills Architecture

## Agent vs Skill vs Tool
- **Agent**: Owns a domain (e.g., `ProductAgent`, `OrderAgent`). Responsible for deciding *which* skill to invoke based on customer intent.
- **Skill**: Owns a workflow or Standard Operating Procedure (SOP). Determines *how* to solve a specific problem (e.g., `ProductInformationSkill`, `WarrantySkill`). A skill is purely declarative and defines the steps, rules, and allowed tools.
- **Tool**: A specific technical capability (e.g., `retrieve_as_context`, `cancel_order`). Tools are executed externally and securely via a registry.

## Architecture

```text
Domain Agent (LLM Reasoning)
  ↓ selects
Skill Definition (Loaded from SKILL.md)
  ↓ orchestrates
Skill Runtime (Python Authorization Boundary)
  ↓ validates policy
Tool Executor (Capability)
```

## Security Boundary
The core security tenet is that **Markdown cannot grant itself permissions**. 
The LLM may read the markdown to understand *how* to operate, but Python strictly enforces whether a requested tool invocation is actually allowed based on the statically parsed metadata in `SKILL.md`.
