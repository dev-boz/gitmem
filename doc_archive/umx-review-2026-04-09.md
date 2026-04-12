# UMX Spec v0.7 — Comprehensive Review
**Date:** 2026-04-09  
**Reviewer:** Kiro  
**Status:** Pre-implementation critical review

---

## Executive Summary

UMX is an **exceptionally well-designed** specification that addresses real pain points in AI agent memory management. The cognitive science grounding, git-native architecture, and PR-based governance are standout features. However, there are **critical implementation risks** and **architectural gaps** that need addressing before v1.

**Overall Assessment:** 8.5/10 — Strong foundation, needs hardening in key areas.

---

## Critical Gaps & Risks

### 🔴 CRITICAL: Session Capture Uncertainty

**Section 19 openly acknowledges the biggest risk:**

> "Session capture methods... This is the biggest implementation risk."

**Problem:** The entire system depends on capturing session transcripts, but:
- Claude Code's session format needs "reverse-engineering"
- Aider needs "adapter needed"
- Most tools don't expose structured session data

**Impact:** Without reliable session capture, the dream pipeline has no input. This is a **foundational dependency**.

**Recommendation:**
1. **Phase 0 deliverable:** Proof-of-concept adapters for Claude Code + Aider before proceeding
2. Add fallback: Manual session logging via `umx log` command for tools without adapters
3. Consider MCP-first strategy: prioritize tools with MCP support (already structured)
4. Document "adapter development guide" as part of Phase 1

---

### 🔴 CRITICAL: Extraction Prompt Not Specified

**Open Question explicitly states:**

> "Extraction prompt design — most implementation-critical piece not yet specified."

**Problem:** The entire dream pipeline's quality depends on the L1 extraction prompt, but it's not in the spec.

**Impact:** 
- No way to validate the design without seeing the prompt
- Risk of hallucination, over-extraction, or under-extraction
- Can't assess whether `source_type` tagging will work reliably

**Recommendation:**
1. **Phase 1 blocker:** Draft extraction prompt before implementing dream pipeline
2. Include prompt in spec as Appendix A (versioned alongside schema)
3. Add prompt testing harness: known sessions → expected facts (already in roadmap Phase 1)
4. Consider few-shot examples in prompt to demonstrate `source_type` distinction

---

### 🟡 HIGH: Composite Score Weight Tuning

**Multiple sections acknowledge:**

> "Weights are configurable. Defaults require empirical tuning."  
> "All weights exposed as config initially."

**Problem:** The composite scoring formula is central to conflict resolution, but all weights are TBD.

**Impact:**
- Can't predict system behavior in conflict scenarios
- Risk of pathological cases (e.g., old high-strength facts always winning)
- `source_type` weights are guesses

**Recommendation:**
1. Start with **conservative defaults** that bias toward human confirmation
2. Phase 2: Implement telemetry dashboard showing score distributions
3. Add `umx tune` command that analyzes `usage.sqlite` and suggests weight adjustments
4. Document weight tuning methodology in spec (A/B testing approach)

---

### 🟡 HIGH: Corroboration Independence Rules Are Fragile

**Section 5 states:**

> "Corroboration requires independent evidence: two sources count as independent only if they have different `source_session` values AND different `source_tool` values, OR the same tool across sessions separated by ≥24 hours."

**Problem:** This is gameable and may not reflect true independence:
- Same LLM hallucination can appear in multiple tools (they all use similar models)
- 24-hour threshold is arbitrary
- Bridge-written facts (`CLAUDE.md`) explicitly excluded, but what about other cross-contamination?

**Recommendation:**
1. Add `source_model` field to provenance (track which LLM extracted the fact)
2. Require different `source_model` OR different `source_type` for true independence
3. Consider content-based independence: facts with >90% text similarity don't corroborate
4. Add `corroboration_confidence` field (0.0-1.0) instead of binary independent/not

---

### 🟡 HIGH: Fragile → Stable Transition May Be Too Lenient

**Section 14 allows stabilization via:**

1. Surviving one dream cycle
2. Independent corroboration
3. Manual confirmation

**Problem:** Rule 1 means a fact can stabilize without any external validation — just by not being contradicted for one cycle.

**Impact:**
- Hallucinated facts that aren't obviously wrong will stabilize
- No incentive for the system to seek corroboration
- `fragile` status becomes meaningless after first cycle

**Recommendation:**
1. **Require at least one of:** corroboration OR manual confirmation OR SotA review for stabilization
2. Keep "survived one cycle" as necessary but not sufficient
3. Add `fragile_age` field: facts fragile for >7 days without corroboration get flagged for review
4. Consider `provisional` intermediate state: survived cycle but not corroborated

---

### 🟡 MEDIUM: Gap Signal Design Is Underspecified

**Section 11 describes gap signals but:**

> "Gap signals SHOULD only be emitted when the agent actually worked around the gap"

**Problem:**
- How does the agent know it "worked around" vs just failed?
- What prevents gap signal spam?
- No rate limiting or deduplication mentioned

**Recommendation:**
1. Add gap signal schema to Section 9 (currently only shown in example)
2. Require `resolution_method` field: `file_read | tool_output | user_provided | external_doc`
3. Deduplicate gaps by semantic similarity (same as fact dedup)
4. Rate limit: max 5 gap signals per session
5. Add `gap_confidence` field: only emit if agent is confident it found the answer

---

### 🟡 MEDIUM: Lint Sub-Phase Scope Creep

**Section 11 (Phase 3b) lists six lint checks:**

1. Semantic contradiction scan
2. Stale file references
3. Orphan fact_id references
4. Tag drift
5. Convention violations
6. (Implied: more to come)

**Problem:**
- "Semantic contradiction scan" is vague — how is this different from `conflicts_with` detection?
- No performance bounds specified (could be O(n²) for large fact sets)
- Weekly cadence may be too slow for fast-moving projects

**Recommendation:**
1. Split Lint into two tiers:
   - **Fast lint** (every cycle): orphan IDs, stale refs, tag drift
   - **Deep lint** (weekly): semantic contradictions, convention violations
2. Specify semantic contradiction algorithm (e.g., cosine similarity >0.85 + opposing sentiment)
3. Add `lint.max_facts_per_scan` config to prevent runaway scans
4. Make lint cadence configurable per-project

---

### 🟡 MEDIUM: Tombstone Resurrection Risk

**Section 21 describes tombstones but:**

> "Tombstones are checked during Gather, re-derivation, and audit."

**Problem:**
- What if a tombstone is deleted (accidentally or maliciously)?
- No mechanism to detect tombstone bypass
- `expires_at` for temporary suppression is risky (what if the fact reappears after expiry?)

**Recommendation:**
1. Add tombstone integrity check: hash of tombstone file in `meta/manifest.json`
2. Tombstone deletions require PR review (never direct commit)
3. Add `tombstone_reason_required: true` config option
4. Consider tombstone audit log: `meta/tombstone-audit.jsonl` tracking all tombstone operations

---

### 🟡 MEDIUM: Secret Redaction Pattern Maintenance

**Section 19 relies on:**

> "Synchronous local pattern scanner MUST run to detect common secret formats"

**Problem:**
- Secret formats evolve (new API key formats, new providers)
- User-defined patterns in `config.yaml` require regex expertise
- False positives will annoy users; false negatives will leak secrets

**Recommendation:**
1. Ship with **regularly updated** secret pattern library (like `gitleaks` or `trufflehog`)
2. Add `umx test-redaction` command: test patterns against sample data
3. Implement pattern confidence scores: high-confidence patterns auto-redact, low-confidence patterns warn
4. Add `redaction.false_positive_feedback` mechanism: users can mark bad redactions
5. Consider integration with existing tools: `gitleaks`, `detect-secrets`

---

## Architectural Concerns

### 🟠 DESIGN: Separate Org vs Separate Repo

**Section 3 mandates:**

> "Memory is completely separate from project repos. They live in different GitHub organisations."

**Concern:** This is a **significant UX barrier**:
- Users must create a new GitHub org (friction)
- Org management overhead (settings, permissions, billing)
- Discoverability: memory repos are "hidden" in a separate org

**Alternative Design:**
- Use a **single repo** per user: `<username>/umx-memory`
- Project memory as subdirectories: `umx-memory/projects/boz/`
- Simpler auth (one repo, one set of permissions)
- Easier discovery (all memory in one place)

**Trade-off Analysis:**

| Aspect | Separate Org (Current) | Single Repo (Alternative) |
|--------|----------------------|--------------------------|
| Setup friction | High (create org) | Low (create repo) |
| Isolation | Strong (org boundary) | Weak (directory boundary) |
| Permissions | Granular (per-repo) | Coarse (per-repo) |
| Discoverability | Low (hidden org) | High (one repo) |
| Scalability | High (many repos) | Medium (one large repo) |
| Git performance | Good (small repos) | Degrades (large repo) |

**Recommendation:**
1. **Keep separate org as default** for multi-project users
2. Add `--single-repo` mode for casual users (all projects in one repo)
3. Document migration path: single-repo → separate org as projects grow
4. Consider hybrid: user memory in personal repo, project memory in separate org

---

### 🟠 DESIGN: JSON as "Derived Cache" Is Risky

**Section 9 states:**

> "Markdown is the canonical storage format. JSON is a derived cache."

**Concern:**
- Markdown parsing is fragile (whitespace, formatting variations)
- Inline metadata in HTML comments is non-standard
- Risk of parse errors corrupting the "source of truth"
- JSON → Markdown → JSON round-trip may not be lossless

**Alternative Design:**
- **JSON as source of truth**, Markdown as presentation layer
- Or: **YAML frontmatter** (standard in static site generators)
- Or: **Structured markdown** (like MDX or Markdoc)

**Recommendation:**
1. **Add strict markdown linter** to enforce canonical format
2. Implement **round-trip tests**: JSON → Markdown → JSON must be identical
3. Consider **YAML frontmatter** for metadata instead of HTML comments:
   ```markdown
   ---
   id: 01JQXYZ...
   strength: 4
   verification: corroborated
   source_type: tool_output
   ---
   postgres runs on port 5433 in dev
   ```
4. Document **canonical markdown format** with examples in spec

---

### 🟠 DESIGN: ULID vs UUID

**Section 5 uses ULID for `fact_id`:**

> "ULID (Universally Unique Lexicographically Sortable Identifier)"

**Concern:**
- ULIDs encode timestamp, which leaks creation time (privacy concern?)
- Less common than UUID (tooling support)
- Sortability benefit is minor (facts have explicit `created` field)

**Recommendation:**
1. **Keep ULID** — sortability is useful for debugging
2. Add note in spec: "ULID timestamp component is not sensitive (facts already have `created` field)"
3. Ensure ULID library is well-maintained (e.g., `python-ulid`)

---

### 🟠 DESIGN: Ovsiankina Bonus May Cause Stale Task Spam

**Section 6 gives open tasks a retrieval bonus:**

> "Facts with `task_status: open` or `blocked` receive a constant additive bonus"

**Concern:**
- Old tasks will dominate injection forever (no decay)
- Auto-abandonment after 30 days is too long
- What if a task is no longer relevant but not explicitly closed?

**Recommendation:**
1. **Apply decay to Ovsiankina bonus**: bonus decreases with task age
2. Reduce `abandon_days` default to 14 days
3. Add `task_last_referenced` field: tasks not mentioned in 7 days get bonus reduced
4. Add `umx tasks --stale` command to review old open tasks

---

## Missing Features (Compared to Competitors)

### 1. **Vector Search** (Mem0, Letta, OpenViking have this)

**What they do:**
- Semantic similarity search using embeddings
- Better than keyword search for conceptual queries
- Handles synonyms, paraphrasing, multilingual

**UMX approach:**
- SQLite FTS (keyword-based)
- "No vector search in v1" (Section 29)

**Assessment:**
- **Acceptable for v1** — FTS covers 80% of use cases
- **Add to roadmap Phase 8+** — vector search as opt-in enhancement
- Consider hybrid: FTS for fast exact match, embeddings for fallback

---

### 2. **Real-time Collaboration** (Letta has multi-user support)

**What they do:**
- Multiple users/agents can share memory
- Conflict resolution for concurrent edits
- Team memory spaces

**UMX approach:**
- Single-user only (Section 29: "No multi-user shared memory in v1")

**Assessment:**
- **Correct prioritization** — single-user is already complex
- **Add to roadmap Phase 9+** — team memory as separate feature
- Consider: project memory in shared org (multiple users with access)

---

### 3. **Memory Summarization** (Letta has hierarchical summarization)

**What they do:**
- Automatically summarize old memories to save space
- Multi-level hierarchy (raw → summarized → principles)

**UMX approach:**
- Atomic facts only (Section 5: "Pipeline MUST NOT merge facts into narratives")
- Summarization is viewer-only (Section 26)

**Assessment:**
- **Strong design choice** — avoids "JPEG compression" problem
- **Keep as-is** — atomic facts are a differentiator
- Consider: optional summarization for `principles/` only (human-reviewed)

---

### 4. **Proactive Memory Suggestions** (OpenViking has "self-evolving")

**What they do:**
- Agent suggests new memories based on patterns
- Proactive gap detection
- Learning from user corrections

**UMX approach:**
- Gap signals are reactive (agent queries, finds nothing, emits gap)
- No proactive suggestion mechanism

**Assessment:**
- **Add to roadmap Phase 7+** — proactive gap detection
- Implement as: L2 dream agent analyzes sessions, proposes "you might want to remember X"
- Requires: session analysis beyond extraction (pattern detection)

---

### 5. **Memory Decay Visualization** (Letta has memory strength UI)

**What they do:**
- Visual representation of memory strength over time
- Decay curves, retrieval history graphs

**UMX approach:**
- Viewer shows strength, but no decay visualization (Section 26)

**Assessment:**
- **Add to Phase 6 (Viewer)** — decay curve visualization
- Show: strength over time, retrieval frequency, corroboration history
- Helps users understand why facts are being pruned

---

### 6. **Git-like Branching for Hypotheticals** (git-context-controller has this)

**What they do:**
- Create memory branches for "what if" scenarios
- Merge branches back to main
- Experiment with different memory states

**UMX approach:**
- Hypothesis branches mentioned (Section 13, 22) but underspecified

**Assessment:**
- **Expand in spec** — hypothesis branches are a killer feature
- Add to Section 13: branch lifecycle, merge rules, conflict resolution
- Use case: "what if we used Postgres instead of MySQL?" — branch memory, test, merge or discard

---

### 7. **Memory Import/Export** (Mem0 has this)

**What they do:**
- Export memory to JSON/CSV for backup
- Import from other systems
- Portability between tools

**UMX approach:**
- Git is the export format (clone the repo)
- `umx import --tool claude-code` mentioned (Section 25) but not detailed

**Assessment:**
- **Add to Phase 3 (Read adapters)** — bulk import is critical
- Specify import format: JSON schema for facts
- Add `umx export --format json` for non-git users

---

## Strengths (What UMX Does Better)

### ✅ **Cognitive Science Grounding**

**Unique to UMX:**
- Explicit mapping to Tulving, Schacter, Anderson, etc.
- `encoding_strength` based on deliberateness (not just confidence)
- Consolidation theory (`fragile` → `stable`)
- Source monitoring (`source_type` enum)

**Why it matters:**
- Provides theoretical foundation for design decisions
- Predicts failure modes (interference, consolidation, source confusion)
- Enables principled tuning (not just trial-and-error)

**Competitors:** None have this level of cognitive science integration.

---

### ✅ **PR-Based Governance (gitmem)**

**Unique to UMX:**
- L1 (cheap model) proposes, L2 (SotA) reviews, L3 (human) resolves
- Full audit trail via GitHub PRs
- No auto-commit to main in remote/hybrid mode

**Why it matters:**
- Prevents hallucination propagation
- Enables correction and rollback
- Transparent decision-making

**Competitors:** 
- Mem0: no governance (direct writes)
- Letta: no PR workflow
- git-context-controller: has git operations but no PR governance

---

### ✅ **Immutable Session Logs**

**Unique to UMX:**
- Raw sessions never edited (except pre-commit redaction)
- Re-derivation always possible
- Audit baseline for fact verification

**Why it matters:**
- Enables "deep therapy" (re-extract with better model)
- Prevents memory drift
- Forensic analysis of memory evolution

**Competitors:**
- Most systems don't preserve raw sessions
- Letta has session logs but they're not immutable

---

### ✅ **Hierarchical Scoping**

**Unique to UMX:**
- User → Machine → Project → Folder → File
- Private/secret split
- Lazy loading for folder/file scopes

**Why it matters:**
- Efficient context injection (only relevant scopes)
- Privacy control (secrets never injected)
- Scales to large projects

**Competitors:**
- Mem0: flat namespace
- Letta: user/agent scopes only
- OpenViking: has hierarchy but not as fine-grained

---

### ✅ **Tool-Agnostic by Design**

**Unique to UMX:**
- Works with any CLI tool (hooks, shims, MCP, manual)
- No vendor lock-in
- Cross-tool memory sharing

**Why it matters:**
- Users can switch tools without losing memory
- Encourages tool diversity
- Future-proof

**Competitors:**
- claude-mem: Claude Code only
- Letta: Letta-specific
- Mem0: requires SDK integration

---

### ✅ **Supersession Chains**

**Unique to UMX:**
- Explicit `supersedes`/`superseded_by` links
- Temporal evolution of facts
- `umx history --fact <id>` walks the chain

**Why it matters:**
- Preserves history without bi-temporal complexity
- Enables "what did we believe on date X?" queries
- Audit trail for fact evolution

**Competitors:** None have explicit supersession chains.

---

## Recommendations Summary

### Immediate (Before Phase 1)

1. ✅ **Draft extraction prompt** (Appendix A in spec)
2. ✅ **Proof-of-concept adapters** for Claude Code + Aider
3. ✅ **Tighten fragile → stable rules** (require corroboration OR review)
4. ✅ **Add corroboration confidence** (not binary)
5. ✅ **Specify gap signal schema** (Section 9)

### Phase 1 Additions

6. ✅ **Round-trip tests** (JSON ↔ Markdown)
7. ✅ **Strict markdown linter**
8. ✅ **Adapter development guide**
9. ✅ **Secret pattern library** (regularly updated)
10. ✅ **Composite score telemetry**

### Phase 2+ Enhancements

11. ✅ **Vector search** (opt-in, Phase 8+)
12. ✅ **Proactive gap detection** (Phase 7+)
13. ✅ **Decay visualization** (Phase 6)
14. ✅ **Hypothesis branch spec** (expand Section 13)
15. ✅ **Memory import/export** (Phase 3)

### Design Considerations

16. ⚠️ **Evaluate single-repo mode** (lower friction for casual users)
17. ⚠️ **Consider YAML frontmatter** (instead of HTML comments)
18. ⚠️ **Reduce abandon_days default** (30 → 14 days)
19. ⚠️ **Apply decay to Ovsiankina bonus** (prevent stale task spam)
20. ⚠️ **Split Lint into fast/deep tiers** (performance)

---

## Competitive Positioning

### UMX vs Mem0

| Feature | UMX | Mem0 |
|---------|-----|------|
| **Governance** | PR-based (L1/L2/L3) | None (direct writes) |
| **Audit trail** | Full (git history + PRs) | Limited |
| **Tool support** | Any CLI (hooks/shims/MCP) | SDK required |
| **Scoping** | Hierarchical (5 levels) | Flat |
| **Cognitive science** | Explicit grounding | Implicit |
| **Vector search** | No (v1) | Yes |
| **Infrastructure** | Git + local | Cloud service |

**Verdict:** UMX is better for **developers who want control, auditability, and tool flexibility**. Mem0 is better for **quick integration and semantic search**.

---

### UMX vs Letta (MemGPT)

| Feature | UMX | Letta |
|---------|-----|------|
| **Memory model** | Cognitive science-based | OS-inspired (paging) |
| **Governance** | PR-based | None |
| **Multi-user** | No (v1) | Yes |
| **Session logs** | Immutable | Mutable |
| **Tool support** | Any CLI | Letta-specific |
| **Summarization** | Atomic facts only | Hierarchical |

**Verdict:** UMX is better for **single-user CLI workflows with strong governance**. Letta is better for **multi-agent systems and long-running conversations**.

---

### UMX vs git-context-controller

| Feature | UMX | git-context-controller |
|---------|-----|----------------------|
| **Scope** | Full memory system | Context management only |
| **Governance** | PR-based (L1/L2/L3) | Git operations (no PRs) |
| **Extraction** | Automated (dream pipeline) | Manual |
| **Branching** | Hypothesis branches | Full git branching |
| **Maturity** | Spec only (not implemented) | Implemented |

**Verdict:** UMX is **more comprehensive** (full memory lifecycle). git-context-controller is **more focused** (context management only) and **already implemented**.

---

### UMX vs OpenViking

| Feature | UMX | OpenViking |
|---------|-----|-----------|
| **Architecture** | Git-native | Filesystem-based |
| **Governance** | PR-based | Not specified |
| **Scoping** | 5 levels | Hierarchical (filesystem) |
| **Self-evolution** | Gap signals (reactive) | Proactive |
| **Tool support** | Any CLI | openclaw-focused |

**Verdict:** UMX is better for **git-native workflows and governance**. OpenViking is better for **filesystem-based context and self-evolution**.

---

## Final Verdict

### What Will Come Back to Bite You

1. **Session capture uncertainty** — if adapters don't work, the whole system fails
2. **Extraction prompt quality** — garbage in, garbage out
3. **Composite score tuning** — wrong weights = wrong conflict resolution
4. **Fragile → stable leniency** — hallucinations will stabilize too easily
5. **Secret redaction maintenance** — patterns will go stale, leaks will happen
6. **Separate org friction** — users will abandon setup if it's too hard

### What Will Make UMX Succeed

1. **Cognitive science grounding** — principled design beats ad-hoc
2. **PR-based governance** — transparency and auditability are killer features
3. **Tool-agnostic** — works with any CLI, no lock-in
4. **Immutable sessions** — re-derivation is a superpower
5. **Hierarchical scoping** — efficient injection, scales to large projects
6. **Supersession chains** — temporal evolution without bi-temporal complexity

### Overall Recommendation

**Proceed with implementation, but:**

1. **Phase 0 must include:** extraction prompt + adapter POCs
2. **Phase 1 must include:** round-trip tests + strict linter
3. **Phase 2 must include:** telemetry + weight tuning
4. **Consider:** single-repo mode for casual users
5. **Monitor:** session capture reliability (biggest risk)

**This is a strong spec.** The cognitive science foundation and git-native architecture are innovative. The main risks are **implementation-dependent** (adapters, prompts, weights), not architectural. With careful execution, UMX can be a **category-defining** memory system for AI agents.

---

## Appendix: Suggested Spec Additions

### A. Extraction Prompt Template (Section 11)

Add a new subsection to Section 11:

```markdown
### Extraction Prompt Template

The L1 extraction prompt MUST follow this structure:

**System prompt:**
You are a memory extraction agent. Your job is to extract atomic facts from a session transcript.

**Rules:**
1. Extract only facts that are verifiable or explicitly stated
2. Tag each fact with a source_type: ground_truth_code | user_prompt | tool_output | llm_inference | external_doc
3. Assign encoding_strength: 1 (incidental mention) to 3 (deliberate statement)
4. Keep facts atomic (one fact per line, ≤200 chars)
5. Follow the project's CONVENTIONS.md for topic taxonomy and phrasing
6. Flag facts that deviate from common practice with schema_conflict: true

**Few-shot examples:**
[Include 3-5 examples showing correct source_type tagging]

**Input:** Session transcript + CONVENTIONS.md
**Output:** JSON array of facts with metadata
```

### B. Adapter Development Guide (Section 25)

Add a new subsection to Section 25:

```markdown
### Adapter Development Guide

To create a new tool adapter:

1. **Identify session log location** (e.g., `~/.tool/sessions/`)
2. **Parse session format** (JSONL, Markdown, proprietary)
3. **Normalize to UMX schema** (Section 19)
4. **Tag source_type** based on evidence:
   - Code reads → `ground_truth_code`
   - User messages → `user_prompt`
   - Tool native memory → `tool_output`
   - LLM reasoning → `llm_inference`
5. **Test with known sessions** (expected facts at expected strengths)
6. **Submit PR** to `umx/adapters/`

**Template:** See `umx/adapters/generic.py`
```

### C. Hypothesis Branch Specification (Section 13)

Expand Section 13 with:

```markdown
### Hypothesis Branches

Hypothesis branches enable "what if" memory exploration.

**Lifecycle:**
1. `umx branch hypothesis/postgres-migration` — create branch
2. Work in branch (facts isolated from main)
3. `umx merge hypothesis/postgres-migration` — merge back (with conflict resolution)
4. OR: `umx branch --discard hypothesis/postgres-migration` — discard

**Merge rules:**
- Facts unique to hypothesis → added to main
- Facts conflicting with main → conflict resolution (composite score)
- Facts superseding main facts → supersession chain created

**Use cases:**
- Architectural exploration ("what if we used X instead of Y?")
- Debugging hypotheses ("what if the bug is in module Z?")
- Temporary context ("working on feature X, need X-specific facts")
```

---

**End of Review**
