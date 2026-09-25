# Product Agent Architecture (Phase D.1)

## Overview

The `ProductAgent` is the first domain agent in the new architecture. It proves the full vertical slice:

```text
Semantic Router (context only)
    ↓
Product domain classification
    ↓
ProductAgent
    ↓
ProductSkillResolver (deterministic, explicit mapping)
    ↓
SkillRegistry (central Phase C registry, single source of truth)
    ↓
ProductInformationSkill (loaded from SKILL.md)
    ↓
SkillRuntime (policy enforcement)
    ↓
ToolExecutor (primary tool derived from skill.allowed_tools)
    ↓
Structured SkillExecutionResult
    ↓
ProductAgent response generation
```

## ProductAgent Responsibilities

| Responsibility | Description |
|---|---|
| **Receive** | Accepts a `ProductDomainContext` already classified as `domain=product` |
| **Resolve** | Uses `ProductSkillResolver` to deterministically select a skill |
| **Execute** | Requests capabilities through `SkillRuntime` (never direct tool calls) |
| **Respond** | Synthesizes a customer-facing response from verified results |

## ProductAgent Does NOT

- Perform semantic routing (that is the Semantic Router's job)
- Decide arbitrary domain ownership
- Bypass SkillRuntime policy enforcement
- Directly import or invoke `retrieve_as_context`, `cancel_order`, etc.
- Mutate transactional state (orders, refunds, tickets)

## ProductAgent vs Product Skill

| Component | Owns |
|---|---|
| **ProductAgent** | Domain reasoning, skill selection, prompt composition, response generation |
| **ProductInformationSkill** | Workflow instructions, allowed/forbidden tools, risk level, operating procedure |

The agent does not duplicate skill workflow rules. It reads them from the resolved `SkillDefinition.instructions` (the SKILL.md markdown body).

## ProductAgent vs Shared Tools

ProductAgent does not own tools. Tools are shared capabilities accessed only through the `SkillRuntime` boundary. The `SkillRuntime` enforces the skill's `allowed_tools` and `forbidden_tools` policy before any tool execution occurs.

## Skill Resolution

`ProductSkillResolver` delegates skill storage to the central `SkillRegistry` (Phase C). It owns only the domain-specific intent → skill-name mapping via `_PRODUCT_INTENT_MAP`.

**Explicit mappings only — no substring fallback.** Unknown intents return `None`.

```text
product_inquiry → ProductInformationSkill
product_information → ProductInformationSkill
product_features → ProductInformationSkill
product_specs → ProductInformationSkill
product_details → ProductInformationSkill
product_availability → ProductInformationSkill
product_question → ProductInformationSkill
technical_support → ProductInformationSkill
product_comparison → ProductInformationSkill
warranty_claim → None (future WarrantySkill)
product_troubleshooting → None (future)
```

**Python chooses the skill deterministically.** The LLM extracts semantic intent, but does not select the skill identifier.

## Execution Boundary

```text
ProductAgent
    ↓ requests "retrieve_as_context"
SkillRuntime.execute_tool(skill, "retrieve_as_context", {...})
    ↓ checks forbidden_tools → PASS
    ↓ checks allowed_tools → PASS
    ↓ checks risk_level → READ_ONLY, no confirmation
    ↓ delegates to ToolExecutor
ToolExecutor("retrieve_as_context", {...})
    ↓ returns result
SkillExecutionResult(status=SUCCESS, structured_output={...})
```

If ProductAgent requests `cancel_order`:
```text
SkillRuntime.execute_tool(skill, "cancel_order", {...})
    ↓ checks forbidden_tools → cancel_order is FORBIDDEN
    ↓ returns POLICY_DENIED immediately
```

## Response Generation

ProductAgent separates three phases:
1. **Domain reasoning** — determine what the customer needs
2. **Capability execution** — retrieve data through SkillRuntime
3. **Response generation** — compose customer-facing response from verified results

Internal details (tool names, routing internals, graph names) are never exposed to the customer.

## Future Integration Points

### WarrantySkill (Phase D.2+)
- Will be registered in `ProductSkillResolver`
- `warranty_claim` intent → WarrantySkill
- Higher risk level (may require confirmation)
- Different tool allowlist

### ProductTroubleshootingSkill (Future)
- Guided troubleshooting flows
- Read-only capability access

### Live Graph Integration (Phase E+)
- ProductAgent will be wired into the production graph
- Legacy agents will be gradually replaced
- Routing policy already maps `product` → `ProductAgent`

## Tool Registry Gap

*(Note: Authoritative tool registration and centralized ToolRegistry infrastructure is a future integration. Phase D.1 validates tool requests against the skill-side allowlist only.)*

## Observability

`ProductAgentResponse.metadata` includes:
- `domain` — always "product"
- `skill_name` — resolved skill name
- `skill_version` — skill semantic version
- `latency_ms` — end-to-end execution time

No credentials, authorization headers, or raw customer content are logged.
