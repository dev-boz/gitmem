# gitmem Addendum A1 — Active Cognitive Steering
### Additions derived from Agent Ways v0.5.0 cross-analysis
### April 2026 · Applies to umx spec v0.9

> This addendum promotes, refines, or introduces mechanisms that move umx from a passive memory substrate toward an active cognitive steering layer. Each item references the spec section it modifies and its implementation phase.
>
> **Status:** Integrated into `gitmem-spec-v0_9.md` (spec_version `0.9.1`). Keep this file as historical design rationale; use the main spec as the working source of truth.

---

## A1.1  Attention-Decay-Aware Re-Injection

**Modifies:** §16 (Injection Architecture), §17 (Context Budget)
**Phase:** 4 (Injection layer)

### Problem

Section 16 defines injection at session start, each prompt, post-tool, pre-compact, and shim startup. All assume that once injected, a fact persists in effective attention until the session ends. This is false. Transformer attention to injected content decays as a power law with both turn count and token distance from the generation cursor (empirically validated by Liu et al. 2023, "Lost in the Middle"; formally grounded in RoPE positional encoding decay).

A fact injected at token 5K has negligible attention weight at token 200K, regardless of context window size.

### Addition

Add a new injection trigger to the §16 table:

| Injection point | Trigger | Layers injected |
|-----------------|---------|-----------------|
| **Re-injection** | Token distance threshold | Previously-injected facts whose `token_distance_since_injection` exceeds `inject.redisclose_pct` of the tool's context window (default: 25%) |

**Rules:**

- Re-injection is budget-constrained: re-injected facts compete with new candidates in the same greedy packing pass. They are not additive — they consume the same `--max-tokens` budget.
- Re-injection candidates are limited to facts that were (a) previously injected in this session AND (b) referenced in agent output (per `meta/usage.sqlite` session tracking). Facts injected but ignored are not re-injected.
- Maximum re-injections per session: `inject.max_redisclosures` (default: 3). This prevents runaway context consumption in very long sessions.
- Token distance is estimated from session event count × average tokens per event, or from tool-reported context usage where available. Precision is not critical — the threshold is a heuristic, not a gate.

**Config:**

```yaml
inject:
  redisclose_pct: 0.25        # re-inject when 25% of context window has elapsed
  max_redisclosures: 3        # cap per session
```

### Rationale

Agent Ways' context decay model demonstrates that timed re-injection near the attention cursor maintains steady-state adherence instead of a damped sawtooth. The 25% figure aligns with empirical retrieval degradation curves (Opus 4.6: ~15% retrieval accuracy drop per quarter-window at 1M context).

---

## A1.2  Injection Saturation Cap

**Modifies:** §17 (Context Budget)
**Phase:** 4 (Injection layer)

### Problem

The greedy packing algorithm (§17) optimises for token budget but not for attention budget. Injecting 20 facts that fit within `--max-tokens` dilutes the effective attention to each individual fact hyperbolically.

### Addition

Add to §17 after the greedy packing algorithm:

**Attention saturation cap.** After the greedy packing pass, if the number of selected facts exceeds `inject.max_concurrent_facts` (default: 12), truncate by dropping the lowest `packing_score` candidates until the cap is met. The cap is applied *after* the always-inject floor — protected facts are never dropped by saturation.

The default of 12 is deliberately generous. Implementations SHOULD track injection-vs-usage ratios in `meta/usage.sqlite` and recommend a tighter cap when data shows diminishing returns.

```yaml
inject:
  max_concurrent_facts: 12    # attention saturation cap
```

### Rationale

Agent Ways formalises this as `A_eff ≈ A_inject / (1 + k·N_concurrent)` — effective per-fact adherence drops hyperbolically with concurrent injections. Their empirical guidance is that 3-5 "ways" is the sweet spot, but ways are larger (20-60 lines each) than umx atomic facts. A higher cap is appropriate for umx's smaller units.

---

## A1.3  Pre-Tool Injection Hook

**Modifies:** §16 (Injection Architecture)
**Phase:** 4 (Injection layer)

### Problem

The current "Post-tool hook" in §16 fires after a tool has already executed. By the time memory about `git commit` conventions is injected, the commit has already been made. Agent Ways' strongest design insight is that guidance delivered *before* tool execution — at the moment the agent has decided to act but hasn't yet — lands at peak attention.

### Addition

Add a new injection trigger to the §16 table:

| Injection point | Trigger | Layers injected |
|-----------------|---------|-----------------|
| **Pre-tool hook** | Tool/command about to execute (Tier 1 tools with hook API) | Facts matching tool signature via keyword overlap; procedures (A1.7) matching command pattern |

**Rules:**

- Pre-tool injection uses the same greedy packing algorithm with a reduced budget: `inject.pre_tool_max_tokens` (default: 1000). Pre-tool injection is supplementary, not a replacement for prompt-time injection.
- Matching is by `keyword_overlap` against the tool name and arguments (e.g., `git commit`, `docker build`, `npm publish`).
- Pre-tool injection is Tier 1 only (tools with hook APIs). Tier 2-4 tools cannot intercept pre-execution.
- Pre-tool injected facts are marked in `meta/usage.sqlite` with `injection_point: pre_tool` for calibration.

```yaml
inject:
  pre_tool_max_tokens: 1000   # budget for pre-tool injection
```

---

## A1.4  Behavioural Failure Modes

**Modifies:** §22 (Failure Modes)
**Phase:** 2 (Dream pipeline) — detection; Phase 4 (Injection layer) — mitigation

### Problem

Section 22 covers structural failures (drift, conflicts, stale facts) but not agent behavioural failure loops — cases where the memory system itself enables or amplifies bad agent behaviour.

### Addition

Append to the §22 table:

| Failure | Cause | Mitigation |
|---------|-------|------------|
| **Memory entrenchment** | Wrong fact reaches S:4 via repeated LLM corroboration without independent evidence | Corroboration from same `source_type` within same session does not count. Cross-source-type requirement: S:4 requires at least one `ground_truth_code` or `human-confirmed` corroboration. Existing §6 independence rules partially cover this — this row makes the protection explicit. |
| **Schema lock-in** | `CONVENTIONS.md` biases extraction so aggressively that novel patterns are never captured | Dream pipeline L2 reviewer SHOULD flag when >80% of extracted facts in a cycle match existing convention categories. Periodic "schema challenge" pass (quarterly or on major refactor) re-evaluates CONVENTIONS.md against recent sessions without convention priors. |
| **Anchor bias** | Agent over-trusts high-strength facts and fails to verify against current code state | Orient phase's ground-truth anchor check (§11) is the primary mitigation. Additionally: facts at S:≥4 that have not been re-verified by `ground_truth_code` in >90 days SHOULD be flagged for re-verification during Lint. |
| **Injected-but-unused accumulation** | Facts repeatedly injected but never referenced in output, wasting budget | `meta/usage.sqlite` calibration (§6) already tracks this. Make operational: after 5 sessions of inject-without-reference, Prune SHOULD demote from hot tier and reduce `relevance_score` weight for that fact. |
| **Subagent amnesia** | Spawned subagent has no access to parent's active context or open tasks | Subagent hand-off protocol (A1.5). |

---

## A1.5  Subagent Hand-Off Protocol

**Modifies:** §16 (Injection Architecture)
**Phase:** 4 (Injection layer)

### Problem

umx assumes a single linear agent session. Tools increasingly spawn subagents (Claude Code `Task`, Codex parallel workers, Gemini delegation). Subagents start with no memory context, re-deriving or missing facts the parent already established.

### Addition

Add to §16:

**Subagent hand-off.** When a Tier 1 tool spawns a subagent and the spawning event is observable (via `SubagentStart` hook or equivalent), umx SHOULD inject into the subagent:

1. All facts with `task_status: open` from the current session (Ovsiankina set).
2. The hot-tier summary (MEMORY.md excerpt, capped at `inject.subagent_hot_tokens`, default: 1500).
3. Any facts injected into the parent in the current turn that were referenced in output (the "active working set").

**Constraints:**

- Subagent injection is a subset of parent injection, never a superset. The subagent MUST NOT receive facts the parent was not authorised to see (respects `local/secret/` exclusion).
- Subagent injection budget: `inject.subagent_max_tokens` (default: 2000). Smaller than parent budget because subagents have narrower scope.
- Subagent-produced facts inherit `source_type` from the subagent's extraction context, not the parent's.

```yaml
inject:
  subagent_max_tokens: 2000
  subagent_hot_tokens: 1500
```

---

## A1.6  Agent Memory Interaction Loop (Normative)

**New section.** Insert after §3 (Architecture Overview) as §3a.
**Phase:** Non-implementation (normative guidance for implementers and tool authors)

### Problem

The spec defines storage, scoring, and governance but not the closed loop of how agents interact with memory. Implementers lack a mental model for where each spec mechanism activates in the agent's cognitive cycle.

### Addition

```
Observe → Retrieve → Act → Reflect → Encode → Consolidate
  │          │         │       │          │          │
  │          │         │       │          │          └─ Dream pipeline (§11)
  │          │         │       │          └─ Session log write (§19)
  │          │         │       └─ Gap signal emission (§11)
  │          │         └─ Agent executes tool / produces output
  │          └─ Injection architecture (§16)
  └─ Session start / prompt / pre-tool / file-read triggers
```

| Loop stage | umx mechanism | Cost substrate |
|------------|---------------|----------------|
| **Observe** | Injection triggers detect context | Cheap (hook scripts, pattern matching) |
| **Retrieve** | Greedy packing, scope resolution, FTS/semantic search | Cheap (SQLite, local computation) |
| **Act** | Agent reasoning and tool use | Expensive (inference tokens) |
| **Reflect** | Gap signal emission when retrieval is incomplete | Cheap (structured output in session log) |
| **Encode** | Session log write, pre-commit redaction | Cheap (file I/O, regex) |
| **Consolidate** | Dream pipeline: Orient → Gather → Consolidate → Lint → Prune | Mixed (L1 cheap model, L2 SotA, L3 human) |

**Substrate separation principle.** Deterministic work (matching, scoring, indexing, redaction) runs in cheap substrates (shell, SQLite, compiled binaries). Inference is reserved for tasks that require reasoning (fact extraction, conflict resolution, schema interpretation). This ratio — many cheap operations enabling fewer expensive ones — is a governing design constraint.

The loop is continuous within a session (Observe through Reflect repeat per turn) and periodic across sessions (Encode and Consolidate run at session boundaries and dream cycles respectively).

---

## A1.7  Procedural Memory — Early Elevation from Phase 9

**Modifies:** §28 (Roadmap), Appendix C (Deferred Considerations)
**Phase:** Move from Phase 9 to Phase 4 (Injection layer) — schema only; implementation remains Phase 9

### Problem

The `procedures/` directory is deferred to Phase 9 (Ecosystem). Agent Ways demonstrates that procedural memory (when/then rules, playbooks) is not an ecosystem concern — it is load-bearing for pre-tool injection (A1.3) and the primary mechanism for encoding project-specific workflows that don't fit the atomic fact model.

### Addition

Define the schema in v1; defer implementation tooling to Phase 9.

**Procedure file format** (stored in `procedures/` alongside `facts/` and `principles/`):

```markdown
# Deploy to staging

<!-- id:01JR... conf:4 src:human_authored -->

## Triggers

- command: `deploy|kubectl apply|helm upgrade`
- file: `k8s/*.yaml|infrastructure/**`
- pattern: `deploy.*(staging|prod)|ship it`

## Steps

1. Run `make test` — never deploy without green tests
2. Check `kubectl config current-context` — must match target cluster
3. Tag the commit: `git tag -a v$(date +%Y%m%d) -m "deploy"`
4. Apply: `kubectl apply -k overlays/staging/`
5. Verify: `kubectl rollout status deployment/app -n staging`
```

**Schema rules:**

- Procedures use the same inline metadata format as facts (§9) for `id`, `conf`, `src`, provenance.
- `## Triggers` section contains one or more trigger types: `command:` (regex on tool invocations), `file:` (glob on file paths), `pattern:` (regex on user prompts).
- Trigger matching is boolean: if any trigger matches, the procedure is injected. Procedures bypass relevance scoring — a matched trigger is top-priority injection, subject only to budget constraints.
- Procedures count toward the injection budget and saturation cap (A1.2).
- Procedures are subject to the same governance as facts: L2 review in `remote`/`hybrid` mode.
- `## Steps` is free-form markdown. It is injected verbatim — umx does not interpret or execute it.

**Roadmap change:** Add "procedures/ schema definition" to Phase 4 deliverables. Implementation tooling (trigger matching engine, procedure authoring CLI) remains Phase 9.

---

## A1.8  Progressive Disclosure in Injection

**Modifies:** §16 (Injection Architecture), §17 (Context Budget)
**Phase:** 4 (Injection layer)

### Problem

All facts are injected at full fidelity. A 200-token fact with full provenance metadata consumes the same budget whether the agent needs the detail or not. This conflicts with the "zero injection by default" principle (§2) — the current system is binary (inject or don't), not graduated.

### Addition

Define three disclosure levels for injected facts:

| Level | Content | Approximate tokens |
|-------|---------|-------------------|
| **L0 — Identifier** | Topic + fact ID only | ~10 |
| **L1 — Atomic fact** | Fact text + strength + source_type | ~30-50 |
| **L2 — Full context** | L1 + provenance + conflicts + encoding context | ~80-200 |

**Injection rules:**

- Default injection is L1. This is the current behaviour.
- When budget pressure is high (remaining budget < 30% of `--max-tokens` after always-inject), downgrade remaining candidates to L0. L0 facts serve as "pointers" — the agent knows a fact exists and can request full disclosure via `umx view <id>`.
- L2 is injected only when: (a) the fact has `consolidation_status: fragile`, (b) the fact has an active `conflicts_with` pointer, or (c) the agent explicitly requests expanded context.
- Progressive disclosure is a packing optimisation. It does not change the scoring algorithm — only the token cost per fact in the greedy packing pass.

**Implementation note:** L0 and L2 are presentation-layer concerns. The fact file format (§9) is unchanged. The injection renderer selects the disclosure level per fact based on budget pressure and fact state.

---

## A1.9  Derived Views (Non-Canonical)

**Modifies:** §29 (Non-Goals — "No narrative merging in storage")
**Phase:** 6 (Viewer / editor)

### Problem

§29 correctly states "Facts are atomic. Narrative synthesis is viewer-only." But the spec never defines what viewer-generated narratives look like or when they're appropriate. This leaves a gap between atomic storage and the synthesised understanding agents need to act effectively.

### Addition

Add to §26 (Viewer / Editor):

**Derived views.** The viewer MAY generate non-canonical derived views from atomic facts. These are computed at read-time, never stored in memory repos, and never injected into agent context.

| View | Source | Purpose |
|------|--------|---------|
| **Topic narrative** | All facts in a topic file, ordered by creation date | Human-readable summary for review |
| **Conflict matrix** | All facts with `conflicts_with` pointers | Visual conflict resolution aid |
| **Task timeline** | All facts with `task_status`, ordered chronologically | "Where was I?" view across sessions |
| **Session replay** | `meta/usage.sqlite` injection log + session events | Which facts were injected, used, or dropped at each turn |

Session replay (inspired by Agent Ways' `ways rethink`) visualises the greedy packer's decisions over time, helping users tune `--max-tokens`, relevance weights, and the saturation cap.

---

## A1.10  Agent Compliance Expectations (Normative)

**New section.** Insert after §23 (Conformance) as §23a.
**Phase:** Non-implementation (normative guidance)

### Problem

The spec defines what memory *is* but not how agents *should behave* when interacting with it. This leaves agent behaviour entirely to tool implementers, with no baseline expectations.

### Addition

Agents consuming umx memory SHOULD follow these norms:

| Expectation | Level | Rationale |
|-------------|-------|-----------|
| Challenge `S:1` facts before acting on them | SHOULD | S:1 facts are single-source, unverified — treat as hypotheses |
| Prefer `ground_truth_code` over `llm_inference` when both are available for the same subject | MUST | Mirrors the hard rule in §6 |
| Surface active conflicts (`conflicts_with`) to the user when the conflicting facts are relevant to the current action | SHOULD | Agents should not silently pick a side |
| Honour `[fragile]` markers — verify via tool call or user check before relying | SHOULD | Already stated in §16; repeated here for agent implementers |
| Emit gap signals when retrieval is incomplete and the agent works around the gap | MAY | Feeds the dream pipeline's gap-triggered extraction |
| Not inject `local/secret/` content into output, logs, or subagent context | MUST NOT | Already enforced structurally; repeated for clarity |

These expectations are normative guidance, not conformance requirements. They do not affect UMX-Read/Write/Full conformance levels (§23).

---

## A1.11  Evaluation Metrics

**New section.** Insert after §26 (Viewer / Editor) as §26a.
**Phase:** 6 (Viewer / editor) — telemetry; Phase 7 (Hardening) — dashboard

### Problem

`meta/usage.sqlite` collects telemetry but the spec defines no metrics derived from it. Without explicit evaluation criteria, there is no way to know if the memory system is actually helping.

### Addition

Define canonical metrics computed from `meta/usage.sqlite`:

| Metric | Formula | Healthy range | Signal |
|--------|---------|---------------|--------|
| **Injection precision** | facts_referenced_in_output / facts_injected | >0.3 | Below 0.3 → relevance scoring is miscalibrated or budget is too large |
| **Fact churn rate** | facts_superseded_per_dream_cycle / total_active_facts | <0.1 | Above 0.1 → codebase is changing faster than memory can stabilise |
| **Contradiction rate** | new_conflicts_per_cycle / facts_extracted_per_cycle | <0.05 | Above 0.05 → extraction quality issue or genuine codebase inconsistency |
| **Entrenchment index** | facts_at_S4_without_ground_truth_corroboration / facts_at_S4 | <0.2 | Above 0.2 → memory entrenchment risk (A1.4) |
| **Hot tier utilisation** | hot_tier_tokens / hot_tier_max_tokens | 0.5–0.9 | Below 0.5 → memory is underused; above 0.9 → trim needed |
| **Staleness ratio** | facts_not_referenced_in_30_days / total_active_facts | <0.4 | Above 0.4 → Prune may be too conservative |

`umx status` SHOULD report these metrics. `umx health` SHOULD flag metrics outside healthy ranges.

---

## Summary of Changes

| Addendum | Spec section | Phase impact | Category |
|----------|-------------|-------------|----------|
| A1.1 Re-injection | §16, §17 | Phase 4 | Injection mechanics |
| A1.2 Saturation cap | §17 | Phase 4 | Injection mechanics |
| A1.3 Pre-tool hook | §16 | Phase 4 | Injection mechanics |
| A1.4 Behavioural failures | §22 | Phase 2/4 | Failure modes |
| A1.5 Subagent hand-off | §16 | Phase 4 | Injection mechanics |
| A1.6 Interaction loop | New §3a | None (normative) | Architecture |
| A1.7 Procedures schema | §28, App C | Phase 4 (schema) / 9 (tooling) | Memory model |
| A1.8 Progressive disclosure | §16, §17 | Phase 4 | Injection mechanics |
| A1.9 Derived views | §26 | Phase 6 | Viewer |
| A1.10 Agent compliance | New §23a | None (normative) | Conformance |
| A1.11 Evaluation metrics | New §26a | Phase 6/7 | Observability |

### What was considered and excluded

| Concept from Agent Ways | Reason for exclusion |
|------------------------|---------------------|
| Way matching engine (BM25 + embeddings) | umx has its own retrieval architecture (§20/20a) which is more appropriate for memory vs guidance |
| Governance policies (architecture.md, code-lifecycle.md) | umx's PR-based governance is simpler and more appropriate for a memory spec |
| Dynamic shell macros on facts | Violates "facts are atomic" principle (§29). Real-time state belongs in tool output, not memory. If needed, a procedure (A1.7) can reference a script in its steps — but umx does not execute it. |
| Knowledge graph integration | Already deferred in Agent Ways (ADR-112 Tier 2). Premature for umx v1. |
| `attend` peer awareness daemon | Requires persistent process — conflicts with §29 "No persistent daemons required." Concurrent-agent coordination is better solved by `meta/processing.jsonl` distributed locking (Phase 5). |
| Regulatory provenance (SOC2, GDPR tags) | Over-specifies for a CLI dev tool. Users can add custom fields to fact metadata without spec support. |
| Intent-based operations (`remember()`, `challenge()`) | These are CLI ergonomics, not spec concerns. `umx` CLI already plans these as commands (§28 Phase 7). |
| First-class task objects | Tasks-as-facts with `task_status` metadata is sufficient for v1. First-class task objects would duplicate state between memory and task-management tools (Jira, Linear, GitHub Issues). |

---

*This addendum is part of the umx specification — [github.com/dev-boz/agent-interface-protocol](https://github.com/dev-boz/agent-interface-protocol)*
