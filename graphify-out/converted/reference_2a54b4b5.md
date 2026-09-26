<!-- converted from reference.docx -->

## Experiment Report





Experiment
Report Name















### Author
### Month DD, YYYY

# Document Control


# Purpose
[Describe what this document records, including the experiment objective, execution, analysis, and decision it supports. Note the product surface and intended scope.]
[Explain why this record is being maintained, who should use it, and how it supports auditability, replication, or future decision-making.]

# Business Context
[Describe the company, product, user journey, and observed problem or opportunity. Summarize the evidence that motivated the experiment and where the issue appears in the experience.]
[Explain why this test surface and intervention were selected, including expected value, operational risk, reversibility, and any important alternatives that remained unchanged.]
# Experiment Summary


# Design Notes
[Describe the experimental design and the single change being tested. Explain how the design supports causal interpretation and which elements remained unchanged.]
[Describe eligibility, assignment, exclusions, runtime, and other choices made to reduce contamination or bias. Note any platform, geography, or device constraints.]

# Hypothesis
[Write an if/then hypothesis naming the control, treatment, audience, expected metric direction, and causal rationale.]

# Success Criteria
[Define the primary success metric, guardrail metrics, decision thresholds, and the conditions required to declare a winner or proceed.]

# Pre-Launch Validation
[Confirm that only the intended treatment differs between variants.]
[Confirm assignment persistence, randomization unit, and allocation logic.]
[Validate exposure, outcome, and guardrail instrumentation.]
[Document traffic exclusions and data-quality filters.]
[Record staging, A/A, or other pre-launch validation results.]

# Sample and Exposure Summary
[Summarize achieved sample size, exposure balance, runtime, and any sample-ratio or eligibility findings. State whether the data are suitable for analysis.]

# Primary Outcome

[Summarize the primary metric result, uncertainty, statistical significance, and practical effect size. Avoid claims beyond the experiment scope.]

# Guardrail Metrics

[Summarize each guardrail result, note any threshold breaches, and explain whether the risk profile supports the proposed decision.]
# Segment Results
[Describe which segments were pre-specified, why they matter, and whether the experiment was powered for subgroup comparisons. Distinguish confirmatory from directional analysis.]

[Summarize the most decision-relevant segment patterns and plausible mechanisms. Note uncertainty, multiplicity, or sample-size caveats.]

# Data Quality and Limitations
[List concurrent launches, operational events, or external factors that could affect interpretation.]
[Describe traffic-mix or eligibility changes during the experiment.]
[Document logging, pipeline, or warehouse incidents and their resolution.]
[State platform, geography, audience, or time-window limitations.]
[Note treatment-specific risks, policy constraints, or generalizability limits.]
[Explain how these limitations affect the recommendation and clearly define where the evidence should and should not be generalized.]

# Interpretation
[Connect the observed results to the hypothesis and proposed mechanism. Explain whether the effect is statistically credible, practically meaningful, and consistent across key measures.]
[Translate the result into business or product impact, including expected upside, cost, reversibility, and remaining uncertainty. State the recommended decision posture.]

# Decision Log


# Post Test Actions
[Describe the rollout or implementation sequence.]
[Define post-launch monitoring metrics, cadence, and duration.]
[List replication or follow-up experiments.]
[Identify additional analysis or product opportunities.]
[Document archival, reporting, and ownership requirements.]

# Appendix A: Metric Definitions
[Metric name 1]: [Define numerator, denominator, eligibility, event source, and calculation.]
[Metric name 2]: [Define numerator, denominator, eligibility, event source, and calculation.]
[Metric name 3]: [Define numerator, denominator, eligibility, event source, and calculation.]
[Metric name 4]: [Define numerator, denominator, eligibility, event source, and calculation.]
[Metric name 5]: [Define numerator, denominator, eligibility, event source, and calculation.]

# Appendix B: Analyst Notes
[Document the statistical method, confidence level, and software or query version used.]
[Record stopping rules, interim reads, or deviations from the analysis plan.]
[Summarize data reconciliation, backfills, and reviewer sign-off.]
[Add any remaining assumptions, caveats, or provenance notes.]

| Version | [Version] |
| --- | --- |
| Prepared By | [Prepared by / team] |
| Reviewers | [Reviewer roles or names] |
| Date Prepared | [Date prepared] |
| Reporting Window | [Reporting start date] to [Reporting end date] |
| Status | [Draft / In review / Final] |
| Field | Details |
| --- | --- |
| Experiment Name | [Experiment name] |
| Experiment Key | [Experiment key] |
| Owner Team | [Owner team] |
| Business Owner | [Business owner] |
| Product Surface | [Product surface] |
| Primary Objective | [Primary objective] |
| Control (Variant A) | [Describe the control experience] |
| Treatment (Variant B) | [Describe the treatment experience] |
| Allocation | [Variant allocation] |
| Unit of Randomization | [Randomization unit] |
| Audience | [Eligible audience] |
| Exclusions | [Traffic or user exclusions] |
| Start Date | [Start date] |
| End Date | [End date] |
| Planned Runtime | [Planned runtime] |
| Actual Runtime | [Actual runtime] |
| Metric | Target/Rule |
| --- | --- |
| Primary Metric | [Define the primary metric threshold and statistical decision rule] |
| Guardrail 1 | [Define guardrail metric and acceptable threshold] |
| Guardrail 2 | [Define guardrail metric and acceptable threshold] |
| Guardrail 3 | [Define guardrail metric and acceptable threshold] |
| Decision Rule | [State the full launch, hold, or rollback rule] |
| Metric | Variant A (Control) | Variant B (Treatment) |
| --- | --- | --- |
| [Sample metric 1] | [Variant A value] | [Variant B value] |
| [Sample metric 2] | [Variant A value] | [Variant B value] |
| [Outcome count] | [Variant A value] | [Variant B value] |
| [Exposure or allocation share] | [Variant A value] | [Variant B value] |
| Sample Ratio Check | [Pass / Fail] | [Pass / Fail] |
| Measure | Variant A | Variant B | Absolute Delta | Relative Delta |
| --- | --- | --- | --- | --- |
| [Primary outcome metric] | [Variant A value] | [Variant B value] | [Absolute delta] | [Relative delta] |
| 95% Confidence Interval | [N/A if not applicable] | [N/A if not applicable] | [Confidence interval] | [N/A if not applicable] |
| Two-Sided p-value | [N/A if not applicable] | [N/A if not applicable] | [p-value] | [N/A if not applicable] |
| Outcome Narrative | [N/A] | [N/A] | [Describe effect and practical significance] | [N/A] |
| Guardrail Metric | Variant A | Variant B | Delta | Status |
| --- | --- | --- | --- | --- |
| [Guardrail metric 1] | [Variant A value] | [Variant B value] | [Delta] | [Pass / Fail] |
| [Guardrail metric 2] | [Variant A value] | [Variant B value] | [Delta] | [Pass / Fail] |
| [Guardrail metric 3] | [Variant A value] | [Variant B value] | [Delta] | [Pass / Fail] |
| [Guardrail metric 4] | [Variant A value] | [Variant B value] | [Delta] | [Pass / Fail] |
| Segment | Control Value | Treatment Value | Absolute Delta | Interpretation |
| --- | --- | --- | --- | --- |
| [Segment 1] | [Control value] | [Treatment value] | [Absolute delta] | [Interpret result] |
| [Segment 2] | [Control value] | [Treatment value] | [Absolute delta] | [Interpret result] |
| [Segment 3] | [Control value] | [Treatment value] | [Absolute delta] | [Interpret result] |
| [Segment 4] | [Control value] | [Treatment value] | [Absolute delta] | [Interpret result] |
| [Segment 5] | [Control value] | [Treatment value] | [Absolute delta] | [Interpret result] |
| Item | Decision |
| --- | --- |
| Final Decision | [State final decision and scope] |
| Decision Date | [Decision date] |
| Approvers | [Approver roles or names] |
| Rollout Type | [Describe rollout or implementation approach] |
| Rollback Trigger | [Define rollback threshold or stop condition] |
| Follow Up | [Describe required follow-up validation] |