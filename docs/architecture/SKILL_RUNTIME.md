# Skill Runtime

The `SkillRuntime` is the boundary between LLM reasoning and system state mutation.

## Responsibilities
1. **Tool Policy Enforcement**: Verifies requested tool against `forbidden_tools` (wins) and `allowed_tools`.
2. **Risk Enforcement**: Checks the `RiskLevel` (`read_only`, `low_risk_mutation`, `high_risk_mutation`, `external_side_effect`).
3. **Execution**: Safely delegates execution to the `ToolExecutor`.
4. **Structured Results**: Returns a `SkillExecutionResult` containing the `SkillExecutionStatus`.

## Execution Statuses
- `SUCCESS`
- `MISSING_REQUIRED_INPUT`
- `INVALID_INPUT`
- `TOOL_NOT_ALLOWED`
- `POLICY_DENIED`
- `CONFIRMATION_REQUIRED`
- `TOOL_FAILURE`
- `WORKFLOW_FAILURE`

## What it is NOT
The `SkillRuntime` does **not**:
- Perform semantic routing.
- Execute actual tool logic directly.
- Execute embedded markdown code.
- Communicate directly with WebSockets.
