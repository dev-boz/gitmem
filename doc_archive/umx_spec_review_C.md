---
name: UMX v0.9 Spec Review
overview: "A structured review of [D:\\\\umx-spec-v0_9.md](D:\\\\umx-spec-v0_9.md): the spec is unusually complete for a v0.x design, but several documentation defects (section numbering, markdown rendering, citations) should be fixed before treating it as normative, and a few technical gaps remain for implementers."
todos:
  - id: fix-section-21
    content: "Insert ## 21 Tombstones and Forgetting heading; verify TOC and any §21 cross-refs"
    status: pending
  - id: fix-source-type-table
    content: Restructure dream_consolidation boundary text so GFM table renders correctly
    status: pending
  - id: fix-citation-2
    content: Resolve [2] mismatch between §1 (JPEG drift) and References (Schacter 1987)
    status: pending
  - id: clarify-fts-usage-paths
    content: "Optional: normative FTS5 external-content pattern + standardise meta/usage.sqlite wording"
    status: pending
isProject: false
---

# Thorough review: umx specification v0.9

## Strengths

- **Clear positioning**: Problem statement ([§1](D:\umx-spec-v0_9.md)) and non-goals ([§29](D:\umx-spec-v0_9.md)) align; the “filesystem + injection protocol, not a service” boundary is repeated consistently.
- **Normative discipline**: RFC 2119 language, explicit MUST/SHOULD tables for conformance ([§23](D:\umx-spec-v0_9.md)), and separation of **trust vs relevance vs retention** scores ([§6](D:\umx-spec-v0_9.md)) reduces ambiguous requirements.
- **Auditability**: Inline HTML-comment metadata ([§9](D:\umx-spec-v0_9.md)), supersession chains, tombstones (content currently under §20a; see below), and session immutability form a coherent evidence story.
- **Honest risk surfacing**: Open Questions ([end of doc](D:\umx-spec-v0_9.md)) and adapter “needs reverse-engineering / TBD” rows ([§10](D:\umx-spec-v0_9.md), [§19](D:\umx-spec-v0_9.md)) correctly flag the hardest implementation work.
- **Semantic layer guardrails**: [§20a](D:\umx-spec-v0_9.md) explicitly walls embeddings off from trust/conflict logic — consistent with [§6](D:\umx-spec-v0_9.md) hard constraints.

---

## Critical documentation defects (fix before “frozen” spec)

### 1. Missing section 21 heading (TOC / anchors broken)

The Table of Contents lists **§21 Tombstones and Forgetting**, but there is **no** `## 21` heading in the file. Section numbering jumps **`## 20a` → `## 22`** ([grep of `^##`](D:\umx-spec-v0_9.md)).

The tombstone content exists (starts at `### Problem` immediately after §20a’s Python package impact), but it is **orphaned** under §20a. **Impact**: TOC links, cross-references (“Section 21”), and mental model of the spec are wrong.

**Recommendation**: Insert `## 21  Tombstones and Forgetting` before `### Problem`, and re-validate all internal `§` references.

### 2. Broken markdown table in `source_type` enum ([§5](D:\umx-spec-v0_9.md))

The `external_doc` row is separated from the table by a full paragraph (`**dream_consolidation** boundary:** ...`). In CommonMark/GFM this typically **breaks the table**, leaving `| external_doc | ...` as a stray row.

**Recommendation**: Move the boundary paragraph **below** the full table, or close the table and start a second table, or use a footnote-style callout after the table.

### 3. Citation mismatch for reference `[2]` ([§1](D:\umx-spec-v0_9.md) vs [§31](D:\umx-spec-v0_9.md))

- [§1](D:\umx-spec-v0_9.md) uses `[2]` for the “JPEG compression” effect of LLM memory.
- [§4](D:\umx-spec-v0_9.md) uses `[2]` for **Schacter (1987)** implicit vs explicit memory.
- [§31 References](D:\umx-spec-v0_9.md) defines `[2]` as **Schacter (1987)** only.

So the Problem Statement cites the wrong source for “JPEG compression.” **Recommendation**: Replace `[2]` in §1 with an appropriate reference (e.g. a dedicated cite to summarisation/drift literature, or reuse [6]/[7] if those are the intended “industry observation” sources), and keep Schacter `[2]` only where Schacter is meant.

---

## Consistency and clarity issues

### `usage.sqlite` path wording

The spec alternates between:

- `meta/usage.sqlite` in the path tree ([§8](D:\umx-spec-v0_9.md), [§6](D:\umx-spec-v0_9.md))
- bare `usage.sqlite` in headings and bullets ([§20](D:\umx-spec-v0_9.md), [§18](D:\umx-spec-v0_9.md))

**Recommendation**: Standardise on **“`meta/usage.sqlite` (per memory repo clone)”** in normative sections, with a one-line note that it lives beside other `meta/` artifacts.

### SQLite FTS5 schema completeness ([§20](D:\umx-spec-v0_9.md))

The fragment shows `CREATE TABLE memories (...)` and `CREATE VIRTUAL TABLE memories_fts USING fts5(content, tags, ...)` **without** the usual FTS5 **external content** linkage (`CONTENT=` / `content_rowid`) or triggers to keep rows in sync. Implementers will need either:

- a normative note that the FTS table is **contentless** with manual sync rules, or
- complete SQL for external-content FTS5 matching the `memories` table.

Flag this as a **spec gap** for Phase 1–3 implementations.

### Appendix A (L1 prompt) vs `source_type` enum

[Appendix A](D:\umx-spec-v0_9.md) omits `dream_consolidation` from the enumerated list. That is likely **intentional** (L1 should not emit it), but **Recommendation**: add one sentence: “`dream_consolidation` is not assigned by L1; it is set by consolidation/dream merges.”

---

## Open items already acknowledged (no surprise)

- Copilot / Gemini formats: **TBD** ([§10](D:\umx-spec-v0_9.md)).
- L1 rate limiting and cross-project dream cadence: **TBD** (Open Questions).
- Trust/relevance/retention **weights** left for empirical tuning — acceptable if called out as non-normative defaults ([§6](D:\umx-spec-v0_9.md), [§27](D:\umx-spec-v0_9.md)).

---

## Suggested fix priority (if you revise the document)

| Priority | Item |
|:--:|--|
| P0 | Add `## 21 Tombstones and Forgetting`; verify TOC anchor |
| P0 | Repair `source_type` table / boundary paragraph layout |
| P0 | Fix `[2]` citation in §1 |
| P1 | Clarify FTS5 + `memories` relationship for implementers |
| P1 | Standardise `meta/usage.sqlite` naming |
| P2 | Appendix A one-liner on `dream_consolidation` |

---

## Bottom line

The document is **architecturally coherent** and unusually strong on governance, provenance, and failure modes. The main issues are **editorial/structural** (missing §21 heading, broken table, wrong citation) plus **one technical incompleteness** (FTS5 wiring). Addressing the P0 items will materially improve implementability and trust in the spec as a single source of truth.
