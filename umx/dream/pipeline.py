"""Dream pipeline: Orient → Gather → Consolidate → Prune.

The pipeline may only:
- extract facts
- deduplicate facts
- reweight facts (composite score)
- prune facts
- normalise minor formatting

It must NOT:
- rewrite facts semantically
- merge facts into narratives
- reinterpret meaning beyond extraction
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from umx.dream.conflict import detect_conflicts, write_conflicts_md
from umx.dream.decay import apply_time_decay
from umx.dream.extract import extract_facts_from_text
from umx.dream.gates import DreamLock, should_dream
from umx.dream.gitignore import GitignoreFilter
from umx.dream.notice import clear_notice, write_dream_log, write_notice
from umx.dream.providers import LLMClient
from umx.memory import (
    add_fact,
    build_memory_md,
    derive_json,
    load_all_facts,
    load_topic_facts,
    read_memory_md,
    save_topic_facts,
    write_memory_md,
)
from umx.models import DreamStatus, Fact, Scope, UmxConfig
from umx.strength import composite_score, should_prune

logger = logging.getLogger("umx.dream")


class DreamPipeline:
    """Four-phase dream pipeline for memory consolidation."""

    def __init__(
        self,
        project_root: Path,
        config: UmxConfig | None = None,
        force: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.umx_dir = self.project_root / ".umx"
        self.local_dir = self.umx_dir / "local"
        self.config = config or UmxConfig()
        self.force = force
        self.lock = DreamLock(self.umx_dir)
        self.gitignore = GitignoreFilter.from_project(self.project_root)

        # Pipeline state
        self.existing_facts: list[Fact] = []
        self.candidate_facts: list[Fact] = []
        self.new_facts: list[Fact] = []
        self.removed_facts: list[Fact] = []
        self.conflicts: list = []
        self.status = DreamStatus.FULL
        self.provider_used = ""
        self.skipped_sources: list[str] = []

    def run(self) -> DreamStatus:
        """Execute the full dream pipeline."""
        if not should_dream(
            self.umx_dir,
            force=self.force,
            time_threshold_hours=self.config.dream_time_hours,
            session_threshold=self.config.dream_session_threshold,
        ):
            logger.info("Dream trigger conditions not met")
            return DreamStatus.FULL

        if not self.lock.acquire():
            logger.warning("Could not acquire dream lock")
            return DreamStatus.FAILED

        try:
            # Phase 1: Orient
            self._orient()

            # Phase 2: Gather
            self._gather()

            # Phase 3: Consolidate
            self._consolidate()

            # Phase 4: Prune
            self._prune()

            # Write results
            self._write_results()

            return self.status

        except Exception as e:
            logger.error(f"Dream pipeline failed: {e}")
            self.status = DreamStatus.FAILED
            write_dream_log(
                self.umx_dir,
                self.status,
                error=str(e),
            )
            return self.status

        finally:
            self.lock.release()

    def _orient(self) -> None:
        """Phase 1: Read MEMORY.md, list .umx/ contents, establish baseline."""
        logger.info("Orient: reading existing memory")

        # Load existing facts from all topic files
        self.existing_facts = load_all_facts(self.umx_dir, Scope.PROJECT_TEAM)

        # Also load local facts
        local_facts = load_all_facts(self.local_dir, Scope.PROJECT_LOCAL)
        self.existing_facts.extend(local_facts)

        logger.info(f"Orient: found {len(self.existing_facts)} existing facts")

    def _gather(self) -> None:
        """Phase 2: Read native memory (S:4), extract from transcripts (S:2-3).

        Native memory reads require no LLM calls.
        """
        logger.info("Gather: collecting from sources")

        # Import adapters
        from umx.adapters.claude_code import ClaudeCodeAdapter
        from umx.adapters.aider import AiderAdapter
        from umx.adapters.generic import GenericAdapter

        # Gather from native tool memory (S:4, no LLM needed)
        adapters = [ClaudeCodeAdapter(), AiderAdapter(), GenericAdapter()]
        for adapter in adapters:
            try:
                native_facts = adapter.read_native_memory(self.project_root)
                self.candidate_facts.extend(native_facts)
                logger.info(
                    f"Gather: {adapter.tool_name} → {len(native_facts)} native facts"
                )
            except Exception as e:
                logger.warning(f"Gather: {adapter.tool_name} failed: {e}")

        # Try LLM extraction from transcripts
        llm_client = LLMClient(self.config)
        if llm_client.is_available():
            try:
                transcript_facts = self._extract_transcripts(llm_client)
                self.candidate_facts.extend(transcript_facts)
            except Exception as e:
                logger.warning(f"Gather: transcript extraction failed: {e}")
                self.skipped_sources.append("transcripts")
                self.status = DreamStatus.PARTIAL
        else:
            logger.info("Gather: no LLM available, native-only mode")
            self.skipped_sources.append("transcripts")
            if not self.candidate_facts and not self.existing_facts:
                self.status = DreamStatus.NATIVE_ONLY
            else:
                self.status = DreamStatus.PARTIAL

        llm_client.close()
        logger.info(f"Gather: {len(self.candidate_facts)} candidate facts")

    def _extract_transcripts(self, llm_client: LLMClient) -> list[Fact]:
        """Extract facts from session transcripts using LLM."""
        facts: list[Fact] = []

        # Check for AIP event logs
        events_path = self.project_root / "workspace" / "events.jsonl"
        if events_path.exists():
            content = events_path.read_text()
            extracted = extract_facts_from_text(
                content,
                source_tool="aip",
                topic="general",
                scope=Scope.PROJECT_TEAM,
                encoding_strength=3,
                llm_client=llm_client,
                config=self.config,
            )
            # Route sensitive facts to local
            for fact in extracted:
                if self.gitignore.filter_sensitive_facts(fact.text):
                    fact.scope = Scope.PROJECT_LOCAL
                facts.append(fact)

        return facts

    def _consolidate(self) -> None:
        """Phase 3: Merge candidates against existing facts.

        - Apply corroboration bonus
        - Resolve conflicts by composite score
        - Route gitignored facts to local/
        - Write atomic facts to topic files
        """
        logger.info("Consolidate: merging candidates")

        existing_texts = {f.text.lower().strip(): f for f in self.existing_facts}
        existing_ids = {f.id for f in self.existing_facts}

        for candidate in self.candidate_facts:
            # Check for duplicate by text
            key = candidate.text.lower().strip()
            if key in existing_texts:
                existing = existing_texts[key]
                # Corroboration: same fact from different tool
                if (
                    candidate.source_tool
                    and candidate.source_tool != existing.source_tool
                    and candidate.source_tool not in existing.corroborated_by
                ):
                    existing.corroborated_by.append(candidate.source_tool)
                    if existing.encoding_strength < 4:
                        existing.encoding_strength = min(
                            existing.encoding_strength + 1, 4
                        )
                    existing.confidence = round(
                        (existing.confidence + candidate.confidence) / 2, 4
                    )
                continue

            # Check for duplicate by ID
            if candidate.id in existing_ids:
                continue

            # Route sensitive facts
            if self.gitignore.filter_sensitive_facts(candidate.text):
                candidate.scope = Scope.PROJECT_LOCAL

            self.new_facts.append(candidate)
            existing_texts[key] = candidate
            existing_ids.add(candidate.id)

        # Detect conflicts
        all_facts = self.existing_facts + self.new_facts
        self.conflicts = detect_conflicts(all_facts, self.config)

        logger.info(
            f"Consolidate: {len(self.new_facts)} new, "
            f"{len(self.conflicts)} conflicts"
        )

    def _prune(self) -> None:
        """Phase 4: Remove weak facts, apply decay, rebuild index."""
        logger.info("Prune: cleaning up")

        all_facts = self.existing_facts + self.new_facts

        # Apply time decay to uncorroborated low-strength facts
        all_facts = apply_time_decay(all_facts, self.config)

        # Prune facts below threshold
        kept: list[Fact] = []
        for fact in all_facts:
            if should_prune(fact, self.config):
                self.removed_facts.append(fact)
            else:
                kept.append(fact)

        # Deduplicate by text
        seen_texts: dict[str, Fact] = {}
        deduped: list[Fact] = []
        for fact in kept:
            key = fact.text.lower().strip()
            if key in seen_texts:
                existing = seen_texts[key]
                # Keep the higher-scoring one
                if composite_score(fact, self.config) > composite_score(
                    existing, self.config
                ):
                    deduped = [f for f in deduped if f.id != existing.id]
                    deduped.append(fact)
                    seen_texts[key] = fact
                    self.removed_facts.append(existing)
                else:
                    self.removed_facts.append(fact)
            else:
                seen_texts[key] = fact
                deduped.append(fact)

        # Update internal state
        self.existing_facts = [
            f for f in deduped if f.scope != Scope.PROJECT_LOCAL
        ]
        local_facts = [f for f in deduped if f.scope == Scope.PROJECT_LOCAL]

        # Write back to topic files
        self._write_topic_files(self.umx_dir, self.existing_facts)
        if local_facts:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            (self.local_dir / "topics").mkdir(exist_ok=True)
            self._write_topic_files(self.local_dir, local_facts)

        logger.info(
            f"Prune: kept {len(deduped)}, removed {len(self.removed_facts)}"
        )

    def _write_topic_files(self, umx_dir: Path, facts: list[Fact]) -> None:
        """Group facts by topic and write to topic files.

        Also removes topic files that no longer have any facts (after dedup/prune).
        """
        topics: dict[str, list[Fact]] = {}
        for fact in facts:
            topics.setdefault(fact.topic, []).append(fact)

        topics_dir = umx_dir / "topics"
        topics_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale topic files
        for existing_md in topics_dir.glob("*.md"):
            topic_name = existing_md.stem
            if topic_name not in topics:
                existing_md.unlink()
                json_path = existing_md.with_suffix(".umx.json")
                if json_path.exists():
                    json_path.unlink()

        for topic, topic_facts in topics.items():
            topic_path = topics_dir / f"{topic}.md"
            save_topic_facts(topic_path, topic, topic_facts)
            derive_json(topic_path, topic_facts)

    def _write_results(self) -> None:
        """Write final results: MEMORY.md, conflicts, logs, notices."""
        now = datetime.now(timezone.utc)

        # Rebuild MEMORY.md
        content = build_memory_md(
            self.umx_dir,
            scope="project_team",
            session_count=0,
            last_dream=now.isoformat(),
        )
        write_memory_md(self.umx_dir, content)

        # Write conflicts
        if self.conflicts:
            write_conflicts_md(self.umx_dir, self.conflicts)

        # Write dream log
        write_dream_log(
            self.umx_dir,
            self.status,
            facts_added=len(self.new_facts),
            facts_removed=len(self.removed_facts),
            facts_conflicted=len(self.conflicts),
            provider=self.provider_used,
            skipped_sources=self.skipped_sources,
        )

        # Handle notices
        if self.status == DreamStatus.FULL:
            clear_notice(self.umx_dir)
        elif self.status == DreamStatus.NATIVE_ONLY:
            write_notice(
                self.umx_dir,
                f"Dream ran in native-only mode — all LLM providers unavailable. "
                f"Run `umx dream --force` to retry.",
            )
        elif self.status == DreamStatus.PARTIAL:
            pending = ", ".join(self.skipped_sources)
            write_notice(
                self.umx_dir,
                f"Dream partial — skipped: {pending}. "
                f"Run `umx dream --force` to retry.",
            )
