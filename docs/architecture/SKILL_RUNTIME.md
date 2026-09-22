# Skill Runtime

The `SkillRuntime` is the boundary between LLM reasoning and system state mutation.

## Responsibilities
1. **Tool Policy Enforcement**: Verifies requested tool against `forbidden_tools` (wins) and `allowed_tools`.
2. **Risk Enforcement**: Checks the `RiskLevel` and confirmation requirements:
   - `read_only` → no confirmation required unless explicitly configured.
   - `low_risk_mutation` → confirmation may be required.
   - `high_risk_mutation` → confirmation may be required.
   - `external_side_effect` → confirmation may be required.
   - *Forbidden/Unlisted tools are always denied regardless of risk level.*
3. **Execution**: Safely delegates execution to the decoupled `ToolExecutor`.
4. **Structured Results**: Returns a typed `SkillExecutionResult`. The `structured_output` field contains bounded, executor-supplied data. Raw system exceptions are sanitized from `failure_code` where appropriate to avoid leaking internal state.

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
