# UMX Specification v0.9 — Technical Review

This document provides a thorough technical review of the Universal Memory Exchange (UMX) v0.9 specification. The review focuses on architectural consistency, data model feasibility, synchronization edge cases, and security.

## 1. Architecture & Consistency Gaps

### 1.1 The Telemetry Paradox (`usage.sqlite` vs. Remote Pipeline)
- **Issue**: Section 6 and Section 20 dictate that `usage.sqlite` (which tracks `last_referenced`, inject counts, and usage frequency) is strictly a local-only build artifact that MUST NOT be committed. However, Section 11 & Section 12 describe the Dream Pipeline (Consolidate, Lint, Prune) running as a **GitHub Action** in remote/hybrid mode.
- **Consequence**: A GitHub Action running the Dream Pipeline cannot access the user's local `usage.sqlite`. As a result, the remote Prune phase will see `usage_frequency = 0` for all facts. The retention score calibration and proper pruning of injected-but-unused facts will be completely broken in team or remote-first deployments.
- **Recommendation**: Telemetry must either be aggregated and pushed autonomously (e.g., standardizing a `meta/usage-sync.jsonl` queue that the pipeline can consume) or the specification must state that `usage_frequency` scoring operates in a degraded mode (defaulting to 0) when the pipeline is detached from client execution.

### 1.2 Git Push Concurrency in Hybrid Mode
- **Issue**: Section 18 states `sessions/` is append-only and therefore results in "zero merge conflicts."
- **Consequence**: While there are no file-content conflicts, Git inherently blocks concurrent non-fast-forward pushes. If two agent sessions complete simultaneously and their `umx` processes attempt to push to `main`, one push will fail. 
- **Recommendation**: Explicitly specify the exact push loop logic for concurrent environments, specifically detailing a `git fetch && git rebase` loop with exponential backoff for the async push queue.

### 1.3 Task Resolution Mechanism
- **Issue**: The Ovsiankina bonus (Section 6) promotes `open` or `blocked` tasks, and automatically abandons them after 30 days. However, the mechanism to transition a task to `resolved` is missing.
- **Consequence**: LLMs often fail to definitively announce a context closure. If there is no automated trigger to mark `resolved` during Consolidate based on context shifts, `MEMORY.md` budgets will be permanently squandered on completed tasks until the 30-day inactivity threshold clears them out.
- **Recommendation**: Provide explicit instructions to the L2 auto-reviewer to evaluate if existing open task facts appear completed in the most recent session payload, thereby closing tasks systematically.

---

## 2. Data Model & Storage Concerns

### 2.1 Inline Metadata Bloat
- **Issue**: Section 9 mandates an extensive list of inline metadata fields encoded as HTML comments directly in Markdown files (e.g., `<!-- umx:{"id":..., "conf":..., "cort":[], "ss":...} -->`).
- **Consequence**: This dramatically violates the core design principle of being a "human-editable" and canonical source of truth. A standard 60-character fact will be overshadowed by ~200-300 characters of inline JSON markup per line.
- **Recommendation**: 
  1. Move non-critical provenance metadata to a shadow directory committed to git (e.g., `meta/provenance.json` mapping IDs to their full metadata string).
  2. Keep only the absolute minimum required HTML in the Markdown: `[S:4|V:cor] Fact statement <!-- umx-id:01JQXYZ... -->`.

### 2.2 Reconciling Manual Edits vs. Supersession
- **Issue**: Section 9 states that if a user edits an existing line, "the parser MUST create a NEW fact... set supersedes pointing to the original... original fact's id and text are preserved for audit."
- **Consequence**: A user editing the Markdown file with their local text editor directly alters the string. On the next parser pass, the script must parse the modification, infer it was an edit of the previous ID, generate a new ID, and *inject the old string back into the Markdown* (because superseded facts remain in the file for audit?). This will cause UX friction and mangle human-structured markdown documents.
- **Recommendation**: Clarify how superseded facts are persisted in the canonical `.md`. Ideally, superseded facts should be extracted into a separate `history.md` or hidden archive, so the main topical markdown documents remain clean.

### 2.3 Token Counting Distortion
- **Issue**: Section 17 suggests `len(text) // 4` for token counting to guide injection.
- **Consequence**: If `text` refers to the raw Markdown string that includes the heavy inline HTML metadata, the injection packing engine will severely miscalculate token usage.
- **Recommendation**: Explicitly state that token estimators MUST strip HTML/UMX metadata comments before making `len()` calculations.

---

## 3. Security, Privacy & Safety

### 3.1 Redaction Bypass on Manual & Direct Edits
- **Issue**: Section 19 provides great detail on pre-commit redaction of raw `.jsonl` session files. It also notes "fact-level redaction... MUST apply BEFORE they enter facts/".
- **Consequence**: In `local` mode, or when a user/agent directly writes to topical Markdown files instead of `sessions/`, the validation pipeline might be bypassed before a git push.
- **Recommendation**: Specify a mandatory `pre-push` git hook that enforces the regex and Shannon entropy scanner over all tracked Markdown files in the memory repository to ensure secrets don't leak via manual human edits. 

### 3.2 Tombstone Regex Danger
- **Issue**: Section 21 describes `meta/tombstones.jsonl` matching via Regex (e.g., `"match":"postgres.*5432"`). 
- **Consequence**: Because tombstones suppress facts during `Gather`, `rederive`, and `audit`, a broad regex written to suppress one bad fact might accidentally suppress completely valid future facts from ever entering the system.
- **Recommendation**: Restrict tombstone matching strictness. Regex tombstones should be applied extremely conservatively, preferring exact semantic ID matching or text equality to avoid silent memory loss.

---

## 4. Minor Inconsistencies & Typos

1. **Section 11 Table**: Refers to "Phase 3a and 3b", but Phase 3 is "Consolidate" and 3b is "Lint", yet the text describes Lint as happening "before Prune", making it Phase 4 functionally.
2. **Level of Conformance Constraint**: Section 23 dictates that `UMX-Full` MUST NOT commit derived artifacts using SQLite, which is physically impossible to restrict at a protocol/conformance layer since it's a CLI filesystem choice. 
3. **Dream mode overrides**: Section 11 mentions the Free API quota rotation (Cerebras, Groq, etc) for pipeline execution. Such deep dependencies on third-party free tiers might cause significant usability flakiness early in adoption. Providing local Ollama fallbacks (as config shows) is excellent but should be emphasized as a "stable" execution path.

> [!TIP]
> **Summary Recommendation for the Roadmap**
> Consider adding a Phase 0 constraint focusing on the implementation of a separate `meta/provenance` store. Reducing the physical footprint of metadata inside the Markdown directly impacts the project's foundational ethos ("Filesystem is the working copy"). Without this, users will shy away from adopting the system natively.
