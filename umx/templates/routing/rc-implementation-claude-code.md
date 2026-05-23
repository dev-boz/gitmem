---
route_card_id: rc-impl-claude-code
schema_version: "0.6"
node_id: deep@claude-code/implementer
task_class: implementation
capability_band: deep
confidence: 0.85
encoding_strength: 3
lifecycle: active
promoted_from: imx-telemetry
promoted_at: 2026-04-27T00:00:00Z
written_by: imx-router
---

# Route Card: implementation → deep@claude-code/implementer

## Summary

Claude Code with the implementer profile performs well on local implementation tasks.
EMA score 0.85 over 90 observations.

## Evidence

- 90 successful implementation tasks
- Average latency: normal
- Failure modes: context_exceeded on very large codebases

## Guidance

Prefer for: implementation, implementation.bugfix, implementation.refactor, test_writing
Avoid for: deployment (no approval chain), architecture_decision (prefer frontier)

## Triggers

- task_class: implementation
- task_class: implementation.bugfix
- task_class: implementation.refactor
- task_class: test_writing
- capability_band: deep
