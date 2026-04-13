from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from umx.config import UMXConfig, load_config
from umx.conventions import ConventionSet, apply_conventions_to_fact, normalize_fact_text, parse_conventions
from umx.dream.conflict import facts_conflict, resolve_conflict
from umx.dream.consolidation import stabilize_facts
from umx.dream.extract import clear_gap_records, gap_records_to_facts, mark_sessions_gathered, session_records_to_facts, source_files_to_facts
from umx.dream.gates import DreamLock, mark_dream_complete, read_dream_state, should_dream
from umx.dream.gitignore import load_gitignore, route_facts
from umx.dream.lint import generate_lint_findings, write_lint_report
from umx.dream.notice import append_notice
from umx.dream.providers import available_provider_notice
from umx.git_ops import (
    changed_paths,
    diff_paths_against_ref,
    git_add_and_commit,
    git_checkout,
    git_create_branch,
    git_current_branch,
    git_path_exists_at_ref,
    git_ref_exists,
    git_restore_path,
)
from umx.governance import PRProposal, classify_pr_labels, generate_l1_pr, generate_l2_review
from umx.manifest import rebuild_manifest
from umx.memory import (
    add_fact,
    find_fact_by_id,
    load_all_facts,
    read_fact_file,
    save_repository_facts,
    write_memory_md,
)
from umx.models import ConsolidationStatus, Fact, SourceType, Verification
from umx.scope import config_path, ensure_repo_structure, find_project_root, project_memory_dir
from umx.search import injected_without_reference_sessions, rebuild_index, usage_snapshot
from umx.strength import apply_corroboration, independent_corroboration, should_prune
from umx.tasks import auto_abandon_tasks
from umx.tombstones import is_suppressed, load_tombstones


@dataclass(slots=True)
class DreamResult:
    status: str
    added: int = 0
    pruned: int = 0
    findings: list[dict[str, str]] = field(default_factory=list)
    message: str | None = None
    pr_proposal: PRProposal | None = None


class DreamPipeline:
    def __init__(self, cwd: Path, config: UMXConfig | None = None):
        self.project_root = find_project_root(cwd)
        self.repo_dir = project_memory_dir(self.project_root)
        self.config = config or load_config(config_path())
        self.conventions = ConventionSet()
        self.new_fact_ids: set[str] = set()
        self._gathered_session_ids: list[str] = []

    def orient(self) -> list[Fact]:
        ensure_repo_structure(self.repo_dir)
        self.conventions = parse_conventions(self.repo_dir / "CONVENTIONS.md")
        facts = load_all_facts(self.repo_dir, include_superseded=True)
        updated: list[Fact] = []
        for fact in facts:
            if fact.source_type == SourceType.GROUND_TRUTH_CODE and fact.code_anchor:
                if not (self.project_root / fact.code_anchor.path).exists():
                    updated.append(fact.clone(consolidation_status=ConsolidationStatus.FRAGILE))
                else:
                    updated.append(fact)
            else:
                updated.append(fact)
        return updated

    def gather(self) -> list[Fact]:
        candidates = gap_records_to_facts(self.repo_dir)
        session_candidates = session_records_to_facts(self.repo_dir, config=self.config)
        self._gathered_session_ids = list(
            {f.source_session for f in session_candidates}
        )
        candidates.extend(session_candidates)

        # Extract facts from source files referenced in sessions
        from umx.sessions import list_sessions
        session_paths = list_sessions(self.repo_dir)
        if session_paths:
            try:
                source_facts = source_files_to_facts(
                    self.repo_dir, self.project_root, session_paths,
                )
                candidates.extend(source_facts)
            except Exception:
                logger.warning("source file extraction failed", exc_info=True)

        # Read native tool memory via adapters
        from umx.adapters import all_adapters
        for adapter in all_adapters():
            try:
                native_facts = adapter.read_native_memory(self.project_root)
                candidates.extend(native_facts)
            except Exception:
                logger.warning("adapter %s failed", adapter, exc_info=True)

        if not candidates:
            append_notice(self.repo_dir, available_provider_notice())

        # Route gitignored-path facts to local/private scope
        gitignore_patterns = load_gitignore(self.project_root)
        if gitignore_patterns:
            candidates = route_facts(candidates, gitignore_patterns)

        # Normalize fact text against conventions
        if self.conventions:
            candidates = [
                c.clone(text=normalize_fact_text(c.text, self.conventions))
                for c in candidates
            ]

        # Apply entity vocabulary conventions
        if self.conventions and self.conventions.entity_vocabulary:
            candidates = [
                apply_conventions_to_fact(c, self.conventions)
                for c in candidates
            ]

        return candidates

    def consolidate(self, facts: list[Fact], candidates: list[Fact]) -> list[Fact]:
        tombstones = load_tombstones(self.repo_dir)
        current = [fact.clone() for fact in facts]
        for candidate in candidates:
            if is_suppressed(candidate, tombstones):
                continue
            merged = False
            for index, existing in enumerate(current):
                if existing.superseded_by is not None:
                    continue
                if existing.topic == candidate.topic and existing.text.lower() == candidate.text.lower():
                    if independent_corroboration(existing, candidate):
                        updated = apply_corroboration(existing, candidate)
                        if updated.consolidation_status == ConsolidationStatus.FRAGILE:
                            updated.consolidation_status = ConsolidationStatus.STABLE
                        current[index] = updated
                    merged = True
                    break
                if facts_conflict(existing, candidate):
                    winner, loser = resolve_conflict(existing.clone(), candidate.clone(), config=self.config)
                    if winner.fact_id == existing.fact_id:
                        current[index] = winner
                        current.append(loser)
                    else:
                        current[index] = loser
                        current.append(winner)
                        self.new_fact_ids.add(winner.fact_id)
                    merged = True
                    break
            if not merged:
                current.append(candidate)
                self.new_fact_ids.add(candidate.fact_id)
        return current

    def lint(self, facts: list[Fact]) -> list[dict[str, str]]:
        findings = generate_lint_findings(
            facts,
            conventions=self.conventions,
            project_root=self.project_root,
        )
        write_lint_report(self.repo_dir, findings)
        return findings

    def schema_lock_in_findings(self, candidates: list[Fact]) -> list[dict[str, str]]:
        if not self.conventions.topics or len(candidates) < 5:
            return []
        matched = 0
        for candidate in candidates:
            if candidate.topic in self.conventions.topics:
                matched += 1
                continue
            if any(candidate.topic.startswith(f"{topic}/") for topic in self.conventions.topics):
                matched += 1
        ratio = matched / max(1, len(candidates))
        if ratio <= 0.8:
            return []
        return [
            {
                "kind": "schema-lock-in",
                "message": (
                    f"{matched}/{len(candidates)} gathered facts matched existing convention topics; "
                    "challenge CONVENTIONS.md coverage before the schema hardens further"
                ),
            }
        ]

    def prune(self, facts: list[Fact], now: datetime) -> tuple[list[Fact], int]:
        usage = usage_snapshot(self.repo_dir)
        usage_references = {
            fact_id: row["last_referenced"]
            for fact_id, row in usage.items()
            if row["last_referenced"]
        }
        facts = auto_abandon_tasks(
            facts,
            now,
            abandon_days=self.config.prune.abandon_days,
            usage_last_referenced={
                fact_id: datetime.fromisoformat(value.replace("Z", "+00:00"))
                for fact_id, value in usage_references.items()
            },
        )

        # Citation telemetry demotion
        uncited_map = {
            entry["fact_id"]: entry
            for entry in injected_without_reference_sessions(self.repo_dir, min_sessions=5)
        }
        demoted_count = 0
        demoted_facts: list[Fact] = []
        for fact in facts:
            if fact.fact_id in uncited_map and fact.encoding_strength > 1:
                demoted_facts.append(fact.clone(encoding_strength=max(1, fact.encoding_strength - 1)))
                demoted_count += 1
            else:
                demoted_facts.append(fact)
        facts = demoted_facts

        active = []
        pruned = 0
        tombstones = load_tombstones(self.repo_dir)
        for fact in facts:
            if is_suppressed(fact, tombstones):
                pruned += 1
                continue
            usage_row = usage.get(fact.fact_id)
            usage_frequency = int(usage_row["cited_count"]) if usage_row is not None else 0
            if fact.superseded_by is None and should_prune(fact, now, usage_frequency=usage_frequency, config=self.config):
                pruned += 1
                continue
            active.append(fact)
        stabilized = stabilize_facts(active, self.new_fact_ids, now)
        save_repository_facts(self.repo_dir, stabilized, auto_commit=False)
        write_memory_md(
            self.repo_dir,
            [fact for fact in stabilized if fact.superseded_by is None],
            last_dream=now.isoformat().replace("+00:00", "Z"),
            session_count=int(read_dream_state(self.repo_dir).get("session_count", 0)),
            config=self.config,
            auto_commit=False,
        )
        rebuild_manifest(self.repo_dir, stabilized, now)
        rebuild_index(self.repo_dir)
        clear_gap_records(self.repo_dir)
        self._demoted_count = demoted_count
        return stabilized, pruned

    def _branch_changes(self) -> list[Path]:
        """Tracked changes that should be captured in a PR branch."""
        tracked: list[Path] = []
        for path in changed_paths(self.repo_dir):
            relative = path.relative_to(self.repo_dir).as_posix()
            if relative.startswith("sessions/"):
                continue
            tracked.append(path)
        return tracked

    def _strip_session_changes_from_branch(self, base_ref: str = "origin/main") -> None:
        """Keep session history out of dream PR branches."""
        if not git_ref_exists(self.repo_dir, base_ref):
            return
        for path in diff_paths_against_ref(self.repo_dir, base_ref, pathspec="sessions"):
            relative = path.relative_to(self.repo_dir).as_posix()
            if git_path_exists_at_ref(self.repo_dir, base_ref, relative):
                git_restore_path(self.repo_dir, base_ref, relative)
            else:
                path.unlink(missing_ok=True)

    def _commit_to_branch(self, branch_name: str, message: str) -> bool:
        """Commit the current memory snapshot to a feature branch."""
        original_branch = git_current_branch(self.repo_dir)
        if not git_create_branch(self.repo_dir, branch_name):
            return False
        try:
            self._strip_session_changes_from_branch()
            committed = git_add_and_commit(self.repo_dir, message=message)
        finally:
            if original_branch:
                git_checkout(self.repo_dir, original_branch)
        return committed

    def _generate_pr(self, facts: list[Fact], session_ids: list[str]) -> PRProposal:
        """Create PR proposal for governance review."""
        return generate_l1_pr(facts, session_ids, self.repo_dir)

    def _push_and_open_pr(self, pr_proposal: PRProposal) -> int | None:
        """Push branch and open a PR on GitHub. Returns PR number or None."""
        if not self.config.org:
            logger.warning("no org configured; skipping PR creation")
            return None
        from umx.github_ops import gh_available, push_branch, create_pr
        if not gh_available():
            logger.warning("gh CLI not available; skipping PR creation")
            return None
        slug = self.repo_dir.name
        if not push_branch(self.repo_dir, pr_proposal.branch):
            logger.warning("failed to push branch %s", pr_proposal.branch)
            return None
        return create_pr(
            self.config.org,
            slug,
            pr_proposal.branch,
            pr_proposal.title,
            pr_proposal.body,
            labels=pr_proposal.labels,
        )

    def _push_sessions_to_main(self) -> bool:
        """Push main branch for session sync (hybrid/remote mode)."""
        from umx.git_ops import git_add_and_commit
        session_paths = changed_paths(self.repo_dir, prefix="sessions/")
        if session_paths:
            git_add_and_commit(
                self.repo_dir,
                paths=session_paths,
                message="umx: sync sessions",
            )
        if not self.config.org:
            return False
        from umx.github_ops import gh_available, push_main
        if not gh_available():
            return False
        return push_main(self.repo_dir)

    def _review_candidate_paths(self, base_ref: str = "origin/main") -> list[Path]:
        paths: list[Path] = []
        for path in diff_paths_against_ref(self.repo_dir, base_ref):
            if not path.exists() or path.suffix != ".md":
                continue
            relative = path.relative_to(self.repo_dir).as_posix()
            if relative.startswith(("facts/topics/", "episodic/topics/", "principles/topics/")):
                paths.append(path)
        return paths

    def review_pr(self, pr_number: int) -> dict[str, object]:
        if self.config.dream.mode not in ("remote", "hybrid"):
            raise RuntimeError("L2 review is only available in remote or hybrid mode")
        if not self.config.org:
            raise RuntimeError("L2 review requires config.org to be set")

        ensure_repo_structure(self.repo_dir)
        self.conventions = parse_conventions(self.repo_dir / "CONVENTIONS.md")

        candidate_paths = self._review_candidate_paths()
        candidate_facts: list[Fact] = []
        for path in candidate_paths:
            candidate_facts.extend(read_fact_file(path, repo_dir=self.repo_dir))

        if not candidate_facts:
            return {
                "status": "skipped",
                "action": "skip",
                "reason": "no governed fact changes detected",
                "pr_number": pr_number,
                "files_changed": [],
                "labels": [],
                "violations": [],
            }

        proposal = PRProposal(
            title=f"[dream/l2] Review PR #{pr_number}",
            body="",
            branch=git_current_branch(self.repo_dir) or "",
            labels=classify_pr_labels(candidate_facts),
            files_changed=[path.relative_to(self.repo_dir).as_posix() for path in candidate_paths],
        )
        decision = generate_l2_review(
            proposal,
            self.conventions,
            candidate_facts,
            new_facts=candidate_facts,
        )

        from umx.github_ops import close_pr, comment_pr, merge_pr

        repo_name = self.repo_dir.name
        action = str(decision["action"])
        reason = str(decision["reason"])

        if action == "approve":
            ok = merge_pr(self.config.org, repo_name, pr_number)
            status = "ok" if ok else "error"
            if not ok:
                reason = f"failed to merge PR #{pr_number}"
        elif action == "reject":
            ok = close_pr(
                self.config.org,
                repo_name,
                pr_number,
                comment=f"L2 review rejected PR #{pr_number}: {reason}",
            )
            status = "ok" if ok else "error"
            if not ok:
                reason = f"failed to close PR #{pr_number}"
        else:
            ok = comment_pr(
                self.config.org,
                repo_name,
                pr_number,
                f"L2 review escalated PR #{pr_number}: {reason}",
            )
            status = "ok" if ok else "error"
            if not ok:
                reason = f"failed to comment on PR #{pr_number}"

        return {
            "status": status,
            "action": action,
            "reason": reason,
            "pr_number": pr_number,
            "files_changed": proposal.files_changed,
            "labels": proposal.labels,
            "violations": list(decision.get("violations", [])),
        }

    def run(self, force: bool = False) -> DreamResult:
        ensure_repo_structure(self.repo_dir)
        lock = DreamLock(self.repo_dir)
        if not should_dream(self.repo_dir, force=force):
            return DreamResult(status="skipped", message="dream gates not met")
        if not lock.acquire():
            return DreamResult(status="skipped", message="dream lock held")
        try:
            now = datetime.now(tz=UTC)
            mode = self.config.dream.mode
            oriented = self.orient()
            candidates = self.gather()
            consolidated = self.consolidate(oriented, candidates)
            findings = self.lint(consolidated)
            findings.extend(self.schema_lock_in_findings(candidates))
            write_lint_report(self.repo_dir, findings)

            pr_proposal = None

            if mode == "remote":
                # Remote: compute the full snapshot locally, then commit it on a PR branch.
                final_facts, pruned = self.prune(consolidated, now)
                new_facts = [f for f in final_facts if f.fact_id in self.new_fact_ids]
                branch_changes = self._branch_changes()
                pr_number = None
                if branch_changes:
                    proposal_facts = new_facts or final_facts
                    pr_proposal = self._generate_pr(proposal_facts, self._gathered_session_ids)
                    if self._commit_to_branch(pr_proposal.branch, message="dream(l1): update memory snapshot"):
                        pr_number = self._push_and_open_pr(pr_proposal)
                if self._gathered_session_ids:
                    mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
                mark_dream_complete(self.repo_dir, now)
                demoted = getattr(self, "_demoted_count", 0)
                msg = f"{len(final_facts)} facts retained"
                if demoted:
                    msg += f", {demoted} demoted"
                if pr_proposal:
                    msg += f", PR: {pr_proposal.title}"
                    if pr_number:
                        msg += f" (#{pr_number})"
                return DreamResult(
                    status="ok",
                    added=len(self.new_fact_ids),
                    pruned=pruned,
                    findings=findings,
                    message=msg,
                    pr_proposal=pr_proposal,
                )

            if mode == "hybrid":
                # Hybrid: sessions push to main (append-only), fact changes go through PRs
                final_facts, pruned = self.prune(consolidated, now)
                new_facts = [f for f in final_facts if f.fact_id in self.new_fact_ids]
                branch_changes = self._branch_changes()
                pr_number = None
                if branch_changes:
                    proposal_facts = new_facts or final_facts
                    pr_proposal = self._generate_pr(proposal_facts, self._gathered_session_ids)
                    if self._commit_to_branch(pr_proposal.branch, message="dream(l1): update memory snapshot"):
                        pr_number = self._push_and_open_pr(pr_proposal)
                # Push sessions to main
                self._push_sessions_to_main()
                if self._gathered_session_ids:
                    mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
                mark_dream_complete(self.repo_dir, now)
                demoted = getattr(self, "_demoted_count", 0)
                msg = f"{len(final_facts)} facts retained"
                if demoted:
                    msg += f", {demoted} demoted"
                if pr_proposal:
                    msg += f", PR: {pr_proposal.title}"
                    if pr_number:
                        msg += f" (#{pr_number})"
                return DreamResult(
                    status="ok",
                    added=len(self.new_fact_ids),
                    pruned=pruned,
                    findings=findings,
                    message=msg,
                    pr_proposal=pr_proposal,
                )

            # Local mode (default): direct write
            final_facts, pruned = self.prune(consolidated, now)
            if self._gathered_session_ids:
                mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
            git_add_and_commit(self.repo_dir, message="umx: dream cycle")
            mark_dream_complete(self.repo_dir, now)
            demoted = getattr(self, "_demoted_count", 0)
            msg = f"{len(final_facts)} facts retained"
            if demoted:
                msg += f", {demoted} demoted"
            return DreamResult(
                status="ok",
                added=len(self.new_fact_ids),
                pruned=pruned,
                findings=findings,
                message=msg,
            )
        finally:
            lock.release()
