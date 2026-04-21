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
from umx.dream.extract import clear_gap_records, gap_records_to_facts, mark_sessions_gathered, session_records_to_facts_with_report, source_files_to_facts
from umx.dream.gates import DreamLock, mark_dream_complete, read_dream_state, should_dream
from umx.dream.gitignore import load_gitignore, route_facts
from umx.dream.lint import generate_lint_findings, mark_lint_complete, should_run as should_run_lint, write_lint_report
from umx.dream.notice import append_notice
from umx.dream.pr_render import GovernancePRBodyError
from umx.dream.processing import (
    active_processing_runs,
    append_processing_event,
    complete_processing_run,
    fail_processing_run,
    processing_log_path,
    start_processing_run,
)
from umx.dream.providers import ProviderUnavailableError, run_l2_review_with_providers
from umx.git_ops import (
    GitSignedHistoryError,
    assert_signed_commit_range,
    changed_paths,
    diff_committed_paths_against_ref,
    diff_paths_against_ref,
    git_add_and_commit,
    git_checkout,
    git_commit_failure_message,
    git_create_branch,
    git_current_branch,
    git_fetch,
    git_path_exists_at_ref,
    git_push,
    git_read_text_at_ref,
    git_ref_exists,
    git_ref_sha,
    git_restore_path,
)
from umx.governance import (
    APPROVAL_REQUIRED_LABELS,
    approval_gate_missing_labels,
    approval_override_audit_note,
    GOVERNANCE_MANAGED_LABELS,
    LABEL_HUMAN_REVIEW,
    LABEL_STATE_APPROVED,
    LABEL_STATE_REVIEWED,
    LABEL_TYPE_DELETION,
    PRProposal,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_ESCALATE,
    REVIEW_ACTION_REJECT,
    assert_governance_pr_body,
    classify_pr_labels,
    desired_governance_labels,
    filter_non_operational_sync_paths,
    generate_l1_pr,
    generate_l2_review,
    is_governed_fact_path,
    review_audit_note,
    session_sync_error,
)
from umx.manifest import rebuild_manifest
from umx.memory import (
    add_fact,
    find_fact_by_id,
    load_all_facts,
    parse_fact_line,
    read_fact_file,
    replace_fact,
    save_repository_facts,
    write_memory_md,
)
from umx.models import ConsolidationStatus, Fact, Provenance, SourceType, Verification
from umx.push_safety import PushSafetyError, assert_push_safe
from umx.schema import detect_schema_state
from umx.scope import (
    config_path,
    ensure_repo_structure,
    find_memory_repo_root,
    find_project_root,
    project_memory_dir,
)
from umx.search import injected_without_reference_sessions, rebuild_index, usage_snapshot
from umx.search_semantic import embeddings_available, ensure_embeddings
from umx.sessions import scheduled_archive_sessions
from umx.strength import apply_corroboration, independent_corroboration, should_prune
from umx.tasks import auto_abandon_tasks
from umx.tombstones import is_suppressed, load_tombstones


def _default_lint_status() -> dict[str, object]:
    return {"ran": False, "reason": "not-started"}


@dataclass(slots=True)
class DreamResult:
    status: str
    added: int = 0
    pruned: int = 0
    findings: list[dict[str, str]] = field(default_factory=list)
    message: str | None = None
    pr_proposal: PRProposal | None = None
    lint: dict[str, object] = field(default_factory=_default_lint_status)


class DreamPipeline:
    def __init__(self, cwd: Path, config: UMXConfig | None = None):
        memory_repo = find_memory_repo_root(cwd)
        if memory_repo is not None:
            self.project_root = memory_repo
            self.repo_dir = memory_repo
        else:
            self.project_root = find_project_root(cwd)
            self.repo_dir = project_memory_dir(self.project_root)
        self.config = config or load_config(config_path())
        self.conventions = ConventionSet()
        self.new_fact_ids: set[str] = set()
        self._gathered_session_ids: list[str] = []
        self._push_block_reason: str | None = None

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
        session_candidates, provider_results = session_records_to_facts_with_report(
            self.repo_dir,
            config=self.config,
        )
        self._provider_results = provider_results
        self._gathered_session_ids = list(
            dict.fromkeys(result.session_id for result in provider_results)
        )
        self._dream_provider = None
        self._dream_partial = False
        if provider_results:
            self._dream_provider = next(
                (result.extracted_by for result in provider_results if not result.native_only),
                "native:session-heuristic",
            )
            self._dream_partial = any(result.native_only for result in provider_results)
        candidates.extend(session_candidates)
        provider_notices = list(
            dict.fromkeys(
                result.notice
                for result in provider_results
                if result.notice
            )
        )
        for notice in provider_notices:
            append_notice(self.repo_dir, notice)

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
        return generate_lint_findings(
            facts,
            conventions=self.conventions,
            repo_dir=self.repo_dir,
            project_root=self.project_root,
        )

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
        active_facts = [fact for fact in stabilized if fact.superseded_by is None]
        if self.config.search.backend == "hybrid" and active_facts:
            if not embeddings_available(self.config):
                logger.warning(
                    "hybrid search requested but embedding provider is unavailable; "
                    "embedding prewarm skipped"
                )
            else:
                result = ensure_embeddings(self.repo_dir, active_facts, config=self.config, force=False)
                if result.needs_rebuild and result.message:
                    logger.warning(result.message)
        write_memory_md(
            self.repo_dir,
            active_facts,
            last_dream=now.isoformat().replace("+00:00", "Z"),
            session_count=int(read_dream_state(self.repo_dir).get("session_count", 0)),
            dream_provider=getattr(self, "_dream_provider", None),
            dream_partial=getattr(self, "_dream_partial", False),
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
        processing_relative = processing_log_path(self.repo_dir).relative_to(self.repo_dir).as_posix()
        for path in changed_paths(self.repo_dir):
            relative = path.relative_to(self.repo_dir).as_posix()
            if relative.startswith("sessions/") or relative == processing_relative:
                continue
            tracked.append(path)
        return tracked

    def _strip_operational_changes_from_branch(self, base_ref: str = "origin/main") -> None:
        """Keep session history and coordination state out of dream PR branches."""
        if not git_ref_exists(self.repo_dir, base_ref):
            return
        session_paths = diff_paths_against_ref(self.repo_dir, base_ref, pathspec="sessions")
        processing_paths = diff_paths_against_ref(self.repo_dir, base_ref, pathspec="meta/processing.jsonl")
        for path in [*session_paths, *processing_paths]:
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
            self._strip_operational_changes_from_branch()
            committed = git_add_and_commit(
                self.repo_dir,
                message=message,
                config=self.config,
            )
        finally:
            if original_branch:
                git_checkout(self.repo_dir, original_branch)
        if committed.failed:
            raise RuntimeError(git_commit_failure_message(committed, context="commit failed"))
        return committed.committed

    def _generate_pr(self, facts: list[Fact], session_ids: list[str]) -> PRProposal:
        """Create PR proposal for governance review."""
        return generate_l1_pr(facts, session_ids, self.repo_dir)

    def _github_repo_ref(self):
        from umx.github_ops import resolve_repo_ref

        return resolve_repo_ref(self.repo_dir, config_org=self.config.org)

    def _push_and_open_pr(self, pr_proposal: PRProposal) -> int | None:
        """Push branch and open a PR on GitHub. Returns PR number or None."""
        self._push_block_reason = None
        from umx.github_ops import (
            GitHubError,
            GitHubRemoteIdentityError,
            assert_no_open_governance_pr_overlap,
            assert_expected_github_origin,
            create_pr,
            gh_available,
            is_github_repo_url,
            push_branch,
        )
        from umx.governance import GovernancePRConflictError

        try:
            repo_ref = assert_expected_github_origin(
                self.repo_dir,
                config_org=self.config.org,
                repo_label="project memory repo",
                operation="PR push",
            )
        except GitHubRemoteIdentityError as exc:
            self._push_block_reason = str(exc)
            logger.warning("%s", exc)
            return None
        repo_owner = repo_ref.owner or self.config.org
        if not repo_owner:
            logger.warning("no org configured; skipping PR creation")
            return None
        try:
            assert_push_safe(
                self.repo_dir,
                project_root=self.project_root,
                base_ref="origin/main",
                branch=pr_proposal.branch,
                head_ref=pr_proposal.branch,
                config=self.config,
                include_bridge=True,
            )
        except PushSafetyError as exc:
            self._push_block_reason = str(exc)
            logger.warning("%s", exc)
            return None
        try:
            assert_signed_commit_range(
                self.repo_dir,
                base_ref="origin/main",
                head_ref=pr_proposal.branch,
                config=self.config,
                operation="PR push",
            )
        except GitSignedHistoryError as exc:
            self._push_block_reason = str(exc)
            logger.warning("%s", exc)
            return None
        try:
            if not gh_available():
                logger.warning("gh CLI not available; skipping PR creation")
                return None
        except GitHubError as exc:
            self._push_block_reason = str(exc)
            logger.warning("%s", exc)
            return None
        if is_github_repo_url(repo_ref.url):
            try:
                assert_no_open_governance_pr_overlap(
                    repo_owner,
                    repo_ref.name,
                    branch=pr_proposal.branch,
                    body=pr_proposal.body,
                    labels=pr_proposal.labels,
                )
            except (GitHubError, GovernancePRBodyError, GovernancePRConflictError) as exc:
                self._push_block_reason = str(exc)
                logger.warning("%s", exc)
                return None
        if not push_branch(self.repo_dir, pr_proposal.branch):
            logger.warning("failed to push branch %s", pr_proposal.branch)
            return None
        try:
            return create_pr(
                repo_owner,
                repo_ref.name,
                pr_proposal.branch,
                pr_proposal.title,
                pr_proposal.body,
                labels=pr_proposal.labels,
            )
        except (GitHubError, GovernancePRBodyError, GovernancePRConflictError) as exc:
            self._push_block_reason = str(exc)
            logger.warning("%s", exc)
            return None

    def _push_paths_to_main(self, paths: list[Path], message: str) -> bool:
        from umx.github_ops import GitHubRemoteIdentityError, assert_expected_github_origin

        if git_current_branch(self.repo_dir) != "main":
            return False
        try:
            assert_expected_github_origin(
                self.repo_dir,
                config_org=self.config.org,
                repo_label="project memory repo",
                operation="main-branch sync",
            )
        except GitHubRemoteIdentityError as exc:
            logger.error("%s", exc)
            return False
        unique_paths: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            candidate = path if path.is_absolute() else self.repo_dir / path
            relative = candidate.relative_to(self.repo_dir).as_posix()
            if relative in seen or not candidate.exists():
                continue
            seen.add(relative)
            unique_paths.append(candidate)
        if unique_paths:
            commit_result = git_add_and_commit(
                self.repo_dir,
                paths=unique_paths,
                message=message,
                config=self.config,
            )
            if commit_result.failed:
                logger.error("%s", git_commit_failure_message(commit_result, context="commit failed"))
                return False
        if not self.config.org:
            return False
        if not git_fetch(self.repo_dir):
            return False
        blocked = filter_non_operational_sync_paths(
            diff_committed_paths_against_ref(self.repo_dir, "origin/main"),
            self.repo_dir,
        )
        if blocked:
            logger.error(session_sync_error(self.config.dream.mode, self.repo_dir, blocked))
            return False
        try:
            assert_push_safe(
                self.repo_dir,
                project_root=self.project_root,
                base_ref="origin/main",
                branch="main",
                config=self.config,
                include_bridge=True,
            )
        except PushSafetyError as exc:
            logger.error("%s", exc)
            return False
        try:
            assert_signed_commit_range(
                self.repo_dir,
                base_ref="origin/main",
                head_ref="HEAD",
                config=self.config,
                operation="main-branch sync",
            )
        except GitSignedHistoryError as exc:
            logger.error("%s", exc)
            return False
        return git_push(self.repo_dir, branch="main")

    def _push_sessions_to_main(
        self,
        *,
        extra_paths: list[Path] | None = None,
        message: str = "umx: sync sessions",
    ) -> bool:
        """Push main branch for session and operational sync (hybrid/remote mode)."""
        session_paths = changed_paths(self.repo_dir, prefix="sessions/")
        return self._push_paths_to_main([*session_paths, *(extra_paths or [])], message)

    def _sync_processing_log_to_main(self, message: str) -> bool:
        return self._push_paths_to_main([processing_log_path(self.repo_dir)], message)

    def _review_changed_paths(self, base_ref: str = "origin/main") -> list[Path]:
        return diff_committed_paths_against_ref(self.repo_dir, base_ref)

    def _review_candidate_paths(self, base_ref: str = "origin/main") -> list[Path]:
        return [
            path
            for path in self._review_changed_paths(base_ref)
            if is_governed_fact_path(path, repo_dir=self.repo_dir)
        ]

    def _review_fact_entries(
        self,
        text: str,
        *,
        path: Path,
    ) -> dict[str, tuple[Fact, str]]:
        entries: dict[str, tuple[Fact, str]] = {}
        for line in text.splitlines():
            fact = parse_fact_line(line, repo_dir=self.repo_dir, path=path)
            if fact is None:
                continue
            key = fact.fact_id if "<!-- umx:" in line else line.strip()
            entries[key] = (fact, line.strip())
        return entries

    def _is_strong_in_place_rewrite(
        self,
        previous_fact: Fact,
        current_fact: Fact,
    ) -> bool:
        if previous_fact.encoding_strength < 3:
            return False
        return any(
            (
                previous_fact.text != current_fact.text,
                previous_fact.encoding_strength != current_fact.encoding_strength,
                previous_fact.verification != current_fact.verification,
                previous_fact.source_type != current_fact.source_type,
                previous_fact.memory_type != current_fact.memory_type,
            )
        )

    def _stamp_review_provenance(
        self,
        facts: list[Fact],
        *,
        reviewed_by: str,
        pr_number: int,
        approval_tier: str = "l2-auto",
    ) -> bool:
        updated = False
        for fact in facts:
            provenance = fact.provenance.to_dict()
            provenance["approved_by"] = reviewed_by
            provenance["approval_tier"] = approval_tier
            provenance["pr"] = str(pr_number)
            updated_fact = fact.clone(
                provenance=Provenance.from_dict(provenance),
            )
            updated = replace_fact(self.repo_dir, updated_fact) or updated
        if updated:
            commit_result = git_add_and_commit(
                self.repo_dir,
                message=f"dream(l2): stamp review provenance for PR #{pr_number}",
                config=self.config,
            )
            if commit_result.failed:
                raise RuntimeError(git_commit_failure_message(commit_result, context="commit failed"))
        return updated

    def review_pr(
        self,
        pr_number: int,
        *,
        force_merge: bool = False,
        force_reason: str | None = None,
        expected_head_sha: str | None = None,
    ) -> dict[str, object]:
        if self.config.dream.mode not in ("remote", "hybrid"):
            raise RuntimeError("L2 review is only available in remote or hybrid mode")
        repo_ref = self._github_repo_ref()
        repo_name = repo_ref.name
        repo_org = repo_ref.owner or self.config.org
        if not repo_org:
            raise RuntimeError("L2 review requires config.org or a GitHub origin remote")
        from umx.github_ops import read_pr_body

        pr_body = read_pr_body(repo_org, repo_name, pr_number)
        if pr_body is None:
            raise RuntimeError(f"failed to read PR body for PR #{pr_number}")
        try:
            assert_governance_pr_body(pr_body, allow_legacy=True)
        except GovernancePRBodyError as exc:
            raise RuntimeError(str(exc)) from exc

        schema_error = self._schema_preflight_result()
        if schema_error is not None:
            raise RuntimeError(schema_error.message)
        ensure_repo_structure(self.repo_dir)
        if not git_fetch(self.repo_dir):
            raise RuntimeError("failed to fetch origin for L2 review")
        if not git_ref_exists(self.repo_dir, "origin/main"):
            raise RuntimeError("L2 review requires origin/main to be available")
        if expected_head_sha and git_ref_sha(self.repo_dir, "HEAD") != expected_head_sha:
            raise RuntimeError("L2 review checkout does not match expected PR head SHA")
        self.conventions = parse_conventions(self.repo_dir / "CONVENTIONS.md")

        changed_paths = self._review_changed_paths()
        candidate_paths = [
            path
            for path in changed_paths
            if is_governed_fact_path(path, repo_dir=self.repo_dir)
        ]
        non_governed_paths = [
            path
            for path in changed_paths
            if not is_governed_fact_path(path, repo_dir=self.repo_dir)
        ]
        files_changed = [path.relative_to(self.repo_dir).as_posix() for path in changed_paths]
        destructive_change = False

        def _review_label_transition(
            current_labels: list[str],
            *,
            managed_labels: list[str],
            human_review: bool,
            preserve_approved: bool,
        ) -> list[str]:
            lifecycle_label = (
                LABEL_STATE_APPROVED
                if preserve_approved and LABEL_STATE_APPROVED in current_labels
                else LABEL_STATE_REVIEWED
            )
            desired_human_review = False if lifecycle_label == LABEL_STATE_APPROVED else human_review
            unmanaged = [
                label
                for label in current_labels
                if label not in GOVERNANCE_MANAGED_LABELS
            ]
            return desired_governance_labels(
                [*unmanaged, *managed_labels],
                lifecycle_label=lifecycle_label,
                human_review=desired_human_review,
            )

        def _escalate_human_review(reason: str) -> dict[str, object]:
            from umx.github_ops import comment_pr, read_pr_labels, reconcile_pr_labels

            audit_note = review_audit_note(REVIEW_ACTION_ESCALATE, pr_number, reason)
            current_labels = read_pr_labels(repo_org, repo_name, pr_number)
            if current_labels is None:
                return {
                    "status": "error",
                    "action": REVIEW_ACTION_ESCALATE,
                    "reason": f"failed to read current labels for PR #{pr_number}",
                    "audit_note": audit_note,
                    "pr_number": pr_number,
                    "files_changed": files_changed,
                    "labels": [],
                    "violations": [],
                }
            desired_labels = _review_label_transition(
                current_labels,
                managed_labels=current_labels,
                human_review=True,
                preserve_approved=False,
            )
            labels_ok = reconcile_pr_labels(
                repo_org,
                repo_name,
                pr_number,
                desired_labels,
            )
            if not labels_ok:
                return {
                    "status": "error",
                    "action": REVIEW_ACTION_ESCALATE,
                    "reason": f"failed to reconcile review labels for PR #{pr_number}",
                    "audit_note": audit_note,
                    "pr_number": pr_number,
                    "files_changed": files_changed,
                    "labels": list(current_labels),
                    "violations": [],
                }
            comment_ok = comment_pr(
                repo_org,
                repo_name,
                pr_number,
                audit_note,
            )
            payload_labels = desired_labels
            status = "ok" if comment_ok else "error"
            failure_reason = reason
            if not comment_ok:
                failure_reason = f"failed to persist review comment for PR #{pr_number}"
            return {
                "status": status,
                "action": REVIEW_ACTION_ESCALATE,
                "reason": failure_reason,
                "audit_note": audit_note,
                "pr_number": pr_number,
                "files_changed": files_changed,
                "labels": payload_labels,
                "violations": [],
            }

        if non_governed_paths:
            reason = (
                "mixed governed and non-governed changes require human review"
                if candidate_paths
                else "non-governed changes require human review"
            )
            return _escalate_human_review(reason)

        existing_facts: list[Fact] = []
        new_facts: list[Fact] = []
        for path in candidate_paths:
            relative = path.relative_to(self.repo_dir).as_posix()
            if relative == "meta/tombstones.jsonl":
                destructive_change = True
                continue
            if not relative.startswith(("facts/topics/", "episodic/topics/", "principles/topics/")):
                continue
            current_entries = self._review_fact_entries(
                path.read_text() if path.exists() else "",
                path=path,
            )
            previous_path = self.repo_dir / relative
            previous_entries = self._review_fact_entries(
                git_read_text_at_ref(self.repo_dir, "origin/main", relative) or "",
                path=previous_path,
            )
            for key, (fact, current_line) in current_entries.items():
                previous_entry = previous_entries.get(key)
                if previous_entry is None or previous_entry[1] != current_line:
                    new_facts.append(fact)
                    if previous_entry is not None:
                        if self._is_strong_in_place_rewrite(previous_entry[0], fact):
                            destructive_change = True
                        if (
                            previous_entry[0].superseded_by != fact.superseded_by
                            and fact.superseded_by is not None
                            and previous_entry[0].encoding_strength >= 3
                        ):
                            destructive_change = True
            for key, (fact, _) in previous_entries.items():
                if key not in current_entries:
                    destructive_change = True
                    existing_facts.append(fact)

        if not existing_facts and not new_facts:
            if candidate_paths:
                reason = "governed non-fact changes require human review"
                return _escalate_human_review(reason)
            return {
                "status": "skipped",
                "action": "skip",
                "reason": "no governed fact changes detected",
                "audit_note": None,
                "pr_number": pr_number,
                "files_changed": [],
                "labels": [],
                "violations": [],
            }

        review_new_facts = new_facts or None
        label_facts = new_facts or existing_facts
        labels = classify_pr_labels(label_facts)
        if destructive_change:
            labels = sorted({*labels, LABEL_TYPE_DELETION})
        proposal = PRProposal(
            title=f"[dream/l2] Review PR #{pr_number}",
            body=pr_body,
            branch=git_current_branch(self.repo_dir) or "",
            labels=labels,
            files_changed=files_changed,
        )
        try:
            decision = run_l2_review_with_providers(
                proposal,
                self.conventions,
                existing_facts,
                review_new_facts,
                self.config,
                fallback_reviewer=lambda pr, conventions, existing_facts, new_facts: generate_l2_review(
                    pr,
                    conventions,
                    existing_facts,
                    new_facts=new_facts,
                ),
            )
        except ProviderUnavailableError as exc:
            raise RuntimeError(str(exc)) from exc

        from umx.github_ops import close_pr, comment_pr, merge_pr, read_pr_labels, reconcile_pr_labels

        action = str(decision.action)
        reason = str(decision.reason)
        reviewed_by = decision.reviewed_by
        audit_note = review_audit_note(action, pr_number, reason)
        review_comment = decision.comment_body
        review_model = decision.model
        review_usage = dict(decision.usage) if decision.usage else None
        fact_notes = [dict(item) for item in decision.fact_notes]
        review_prompt_id = decision.prompt_id
        review_prompt_version = decision.prompt_version
        payload_labels = list(proposal.labels)
        merge_blocked = False
        merge_override_used = False
        merge_override_reason = None
        merge_required_labels = sorted(APPROVAL_REQUIRED_LABELS)

        def _review_payload(
            status_value: str,
            reason_value: str,
            labels_value: list[str],
            *,
            merge_blocked_value: bool | None = None,
            merge_override_used_value: bool | None = None,
            merge_override_reason_value: str | None = None,
        ) -> dict[str, object]:
            payload = {
                "status": status_value,
                "action": action,
                "reason": reason_value,
                "audit_note": audit_note,
                "reviewed_by": reviewed_by,
                "pr_number": pr_number,
                "files_changed": proposal.files_changed,
                "labels": labels_value,
                "violations": list(decision.violations),
                "model_backed": decision.model_backed,
                "review_model": review_model,
                "review_usage": review_usage,
                "review_prompt_id": review_prompt_id,
                "review_prompt_version": review_prompt_version,
                "fact_notes": fact_notes,
                "merge_blocked": merge_blocked if merge_blocked_value is None else merge_blocked_value,
                "merge_override_used": (
                    merge_override_used
                    if merge_override_used_value is None
                    else merge_override_used_value
                ),
                "merge_override_reason": (
                    merge_override_reason
                    if merge_override_reason_value is None
                    else merge_override_reason_value
                ),
                "merge_required_labels": merge_required_labels,
            }
            if decision.model_backed:
                append_processing_event(
                    self.repo_dir,
                    {
                        "tier": "l2",
                        "event": "review_completed",
                        "status": status_value,
                        "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                        "pr_number": pr_number,
                        "action": action,
                        "reviewed_by": reviewed_by,
                        "review_model": review_model,
                        "review_usage": review_usage,
                        "review_prompt_id": review_prompt_id,
                        "review_prompt_version": review_prompt_version,
                        "fact_notes": fact_notes,
                        "merge_blocked": merge_blocked if merge_blocked_value is None else merge_blocked_value,
                        "merge_override_used": (
                            merge_override_used
                            if merge_override_used_value is None
                            else merge_override_used_value
                        ),
                        "merge_override_reason": (
                            merge_override_reason
                            if merge_override_reason_value is None
                            else merge_override_reason_value
                        ),
                        "merge_required_labels": merge_required_labels,
                    },
                )
            return payload

        def _review_label_state(
            *,
            human_review: bool,
            preserve_approved: bool,
        ) -> tuple[list[str], list[str]] | None:
            current_labels = read_pr_labels(repo_org, repo_name, pr_number)
            if current_labels is None:
                return None
            desired = _review_label_transition(
                current_labels,
                managed_labels=labels,
                human_review=human_review,
                preserve_approved=preserve_approved,
            )
            return list(current_labels), desired

        if action == REVIEW_ACTION_APPROVE:
            label_state = _review_label_state(human_review=False, preserve_approved=True)
            if label_state is None:
                return _review_payload(
                    "error",
                    f"failed to read current labels for PR #{pr_number}",
                    [],
                )
            current_labels, final_labels = label_state
            current_branch = git_current_branch(self.repo_dir)
            if not current_branch or current_branch == "HEAD":
                return _review_payload(
                    "error",
                    "cannot push review provenance from detached HEAD",
                    current_labels,
                )
            stamped = self._stamp_review_provenance(
                new_facts,
                reviewed_by=reviewed_by,
                pr_number=pr_number,
            )
            match_head_commit = git_ref_sha(self.repo_dir, "HEAD") if stamped else expected_head_sha
            if not match_head_commit:
                match_head_commit = git_ref_sha(self.repo_dir, "HEAD")
            if stamped:
                if not git_push(self.repo_dir, branch=current_branch):
                    return _review_payload(
                        "error",
                        f"failed to push review provenance on {current_branch}",
                        current_labels,
                    )
            reconcile_label_state = _review_label_state(human_review=False, preserve_approved=True)
            if reconcile_label_state is None:
                return _review_payload(
                    "error",
                    f"failed to read current labels for PR #{pr_number}",
                    current_labels,
                )
            current_labels, final_labels = reconcile_label_state
            labels_ok = reconcile_pr_labels(
                repo_org,
                repo_name,
                pr_number,
                final_labels,
                current_labels=current_labels,
            )
            if not labels_ok:
                return _review_payload(
                    "error",
                    f"failed to reconcile review labels for PR #{pr_number}",
                    current_labels,
                )
            payload_labels = list(final_labels)
            if review_comment and not comment_pr(repo_org, repo_name, pr_number, review_comment):
                return _review_payload(
                    "error",
                    f"failed to persist review comment for PR #{pr_number}",
                    payload_labels,
                )
            live_labels = read_pr_labels(repo_org, repo_name, pr_number)
            if live_labels is None:
                return _review_payload(
                    "error",
                    f"failed to read current labels for PR #{pr_number}",
                    [],
                )
            payload_labels = _review_label_transition(
                live_labels,
                managed_labels=labels,
                human_review=False,
                preserve_approved=True,
            )
            missing_approval_labels = approval_gate_missing_labels(live_labels)
            if missing_approval_labels and not force_merge:
                merge_blocked = True
                status = "blocked"
                reason = f"awaiting approval label before merge: {', '.join(missing_approval_labels)}"
            else:
                if missing_approval_labels:
                    if not force_reason:
                        return _review_payload(
                            "error",
                            "approval-gated merge override requires --force-reason",
                            payload_labels,
                            merge_blocked_value=True,
                            merge_override_used_value=False,
                            merge_override_reason_value=force_reason,
                        )
                    override_note = approval_override_audit_note(pr_number, force_reason)
                    if not comment_pr(repo_org, repo_name, pr_number, override_note):
                        return _review_payload(
                            "error",
                            f"failed to persist approval override audit for PR #{pr_number}",
                            payload_labels,
                            merge_blocked_value=True,
                            merge_override_used_value=False,
                            merge_override_reason_value=None,
                        )
                    append_processing_event(
                        self.repo_dir,
                        {
                            "tier": "l2",
                            "event": "approval_override_used",
                            "status": "ok",
                            "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                            "pr_number": pr_number,
                            "reason": force_reason,
                        },
                    )
                    merge_override_used = True
                    merge_override_reason = force_reason
                ok = merge_pr(
                    repo_org,
                    repo_name,
                    pr_number,
                    admin=merge_override_used,
                    match_head_commit=match_head_commit,
                )
                status = "ok" if ok else "error"
                if not ok:
                    reason = f"failed to merge PR #{pr_number}"
        elif action == REVIEW_ACTION_REJECT:
            ok = close_pr(
                repo_org,
                repo_name,
                pr_number,
                comment=review_comment or audit_note,
            )
            status = "ok" if ok else "error"
            if not ok:
                reason = f"failed to close PR #{pr_number}"
        else:
            label_state = _review_label_state(human_review=True, preserve_approved=False)
            if label_state is None:
                return _review_payload(
                    "error",
                    f"failed to read current labels for PR #{pr_number}",
                    [],
                )
            current_labels, final_labels = label_state
            labels_ok = reconcile_pr_labels(
                repo_org,
                repo_name,
                pr_number,
                final_labels,
            )
            if not labels_ok:
                return _review_payload(
                    "error",
                    f"failed to reconcile review labels for PR #{pr_number}",
                    current_labels,
                )
            payload_labels = list(final_labels)
            comment_ok = comment_pr(
                repo_org,
                repo_name,
                pr_number,
                review_comment or audit_note,
            )
            if not comment_ok:
                return _review_payload(
                    "error",
                    f"failed to persist review comment for PR #{pr_number}",
                    payload_labels,
                )
            status = "ok"

        return _review_payload(status, reason, payload_labels)

    def _schema_preflight_result(self) -> DreamResult | None:
        ensure_repo_structure(self.repo_dir, ensure_schema=False)
        schema_state = detect_schema_state(self.repo_dir)
        if schema_state.state == "current":
            return None
        fix_hint = (
            f"run `umx doctor --cwd {self.project_root} --fix`"
            if schema_state.fixable
            else "upgrade gitmem before running dream again"
        )
        return DreamResult(
            status="error",
            message=f"{schema_state.message}; {fix_hint}",
        )

    def run(self, force: bool = False, force_lint: bool = False) -> DreamResult:
        schema_error = self._schema_preflight_result()
        if schema_error is not None:
            return schema_error
        ensure_repo_structure(self.repo_dir)
        mode = self.config.dream.mode
        run_branch = git_current_branch(self.repo_dir)
        if mode in {"remote", "hybrid"} and run_branch != "main":
            return DreamResult(
                status="error",
                message=f"{mode} mode dream must run from main; current branch is {run_branch or 'detached'}",
            )
        if not should_dream(self.repo_dir, force=force):
            return DreamResult(status="skipped", message="dream gates not met")
        lock = DreamLock(self.repo_dir)
        if not lock.acquire():
            return DreamResult(status="skipped", message="dream lock held")
        shared_refs = ("origin/main",) if mode in {"remote", "hybrid"} else ()
        processing_run_id: str | None = None
        self._push_block_reason = None
        try:
            if active_processing_runs(self.repo_dir, refs=shared_refs):
                return DreamResult(status="skipped", message="dream processing held")
            processing_run_id = start_processing_run(
                self.repo_dir,
                mode=mode,
                force=force,
                branch=run_branch,
            )
            if mode in {"remote", "hybrid"} and self.config.org:
                if not self._sync_processing_log_to_main("umx: dream processing start"):
                    fail_processing_run(
                        self.repo_dir,
                        processing_run_id,
                        mode=mode,
                        branch=run_branch,
                        error="failed to publish dream processing start",
                    )
                    return DreamResult(
                        status="error",
                        message="failed to publish dream processing start",
                    )

            now = datetime.now(tz=UTC)
            oriented = self.orient()
            candidates = self.gather()
            lock.heartbeat()
            consolidated = self.consolidate(oriented, candidates)
            lint_ran, lint_reason = should_run_lint(
                self.repo_dir,
                interval=self.config.dream.lint_interval,
                force=force_lint,
                now=now,
            )
            lint_status: dict[str, object] = {"ran": lint_ran, "reason": lint_reason}
            findings: list[dict[str, str]] = []
            if lint_ran:
                findings = self.lint(consolidated)
                findings.extend(self.schema_lock_in_findings(candidates))
                write_lint_report(self.repo_dir, findings)
                mark_lint_complete(self.repo_dir, now)
            lock.heartbeat()

            provider_results = getattr(self, "_provider_results", [])
            provider_summary = None
            if provider_results:
                if any(not result.native_only for result in provider_results):
                    provider_summary = "provider-backed"
                else:
                    provider_summary = "native-only"

            pr_proposal = None

            if mode == "remote":
                # Remote: compute the full snapshot locally, then commit it on a PR branch.
                final_facts, pruned = self.prune(consolidated, now)
                lock.heartbeat()
                new_facts = [f for f in final_facts if f.fact_id in self.new_fact_ids]
                branch_changes = self._branch_changes()
                pr_number = None
                if branch_changes:
                    proposal_facts = new_facts or final_facts
                    pr_proposal = self._generate_pr(proposal_facts, self._gathered_session_ids)
                    if self._commit_to_branch(pr_proposal.branch, message="dream(l1): update memory snapshot"):
                        pr_number = self._push_and_open_pr(pr_proposal)
                    if self._push_block_reason:
                        fail_processing_run(
                            self.repo_dir,
                            processing_run_id,
                            mode=mode,
                            branch=run_branch,
                            error=self._push_block_reason,
                        )
                        return DreamResult(
                            status="error",
                            added=len(self.new_fact_ids),
                            pruned=pruned,
                            findings=findings,
                            message=self._push_block_reason,
                            pr_proposal=pr_proposal,
                            lint=lint_status,
                        )
                if self._gathered_session_ids:
                    mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
                mark_dream_complete(self.repo_dir, now)
                archive_result = scheduled_archive_sessions(
                    self.repo_dir,
                    now=now,
                    config=self.config,
                )
                archived_sessions = int(archive_result.get("archived_sessions", 0))
                demoted = getattr(self, "_demoted_count", 0)
                msg = f"{len(final_facts)} facts retained"
                if provider_summary:
                    msg += f", extraction: {provider_summary}"
                if demoted:
                    msg += f", {demoted} demoted"
                if archived_sessions:
                    msg += f", {archived_sessions} archived"
                if pr_proposal:
                    msg += f", PR: {pr_proposal.title}"
                    if pr_number:
                        msg += f" (#{pr_number})"
                result = DreamResult(
                    status="ok",
                    added=len(self.new_fact_ids),
                    pruned=pruned,
                    findings=findings,
                    message=msg,
                    pr_proposal=pr_proposal,
                    lint=lint_status,
                )
                complete_processing_run(
                    self.repo_dir,
                    processing_run_id,
                    mode=mode,
                    branch=run_branch,
                    added=result.added,
                    pruned=result.pruned,
                    message=result.message,
                    pr_branch=pr_proposal.branch if pr_proposal else None,
                    pr_number=pr_number,
                    dream_provider=getattr(self, "_dream_provider", None),
                    dream_partial=getattr(self, "_dream_partial", False),
                )
                if self.config.org and not self._sync_processing_log_to_main("umx: dream processing complete"):
                    return DreamResult(
                        status="error",
                        added=result.added,
                        pruned=result.pruned,
                        findings=findings,
                        message="failed to publish dream processing completion",
                        pr_proposal=pr_proposal,
                        lint=lint_status,
                    )
                return result

            if mode == "hybrid":
                # Hybrid: sessions push to main (append-only), fact changes go through PRs
                final_facts, pruned = self.prune(consolidated, now)
                lock.heartbeat()
                new_facts = [f for f in final_facts if f.fact_id in self.new_fact_ids]
                branch_changes = self._branch_changes()
                pr_number = None
                if branch_changes:
                    proposal_facts = new_facts or final_facts
                    pr_proposal = self._generate_pr(proposal_facts, self._gathered_session_ids)
                    if self._commit_to_branch(pr_proposal.branch, message="dream(l1): update memory snapshot"):
                        pr_number = self._push_and_open_pr(pr_proposal)
                    if self._push_block_reason:
                        fail_processing_run(
                            self.repo_dir,
                            processing_run_id,
                            mode=mode,
                            branch=run_branch,
                            error=self._push_block_reason,
                        )
                        return DreamResult(
                            status="error",
                            added=len(self.new_fact_ids),
                            pruned=pruned,
                            findings=findings,
                            message=self._push_block_reason,
                            pr_proposal=pr_proposal,
                            lint=lint_status,
                        )
                if self._gathered_session_ids:
                    mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
                mark_dream_complete(self.repo_dir, now)
                demoted = getattr(self, "_demoted_count", 0)
                msg = f"{len(final_facts)} facts retained"
                if provider_summary:
                    msg += f", extraction: {provider_summary}"
                if demoted:
                    msg += f", {demoted} demoted"
                if pr_proposal:
                    msg += f", PR: {pr_proposal.title}"
                    if pr_number:
                        msg += f" (#{pr_number})"
                result = DreamResult(
                    status="ok",
                    added=len(self.new_fact_ids),
                    pruned=pruned,
                    findings=findings,
                    message=msg,
                    pr_proposal=pr_proposal,
                    lint=lint_status,
                )
                complete_processing_run(
                    self.repo_dir,
                    processing_run_id,
                    mode=mode,
                    branch=run_branch,
                    added=result.added,
                    pruned=result.pruned,
                    message=result.message,
                    pr_branch=pr_proposal.branch if pr_proposal else None,
                    pr_number=pr_number,
                    dream_provider=getattr(self, "_dream_provider", None),
                    dream_partial=getattr(self, "_dream_partial", False),
                )
                if self.config.org and not self._push_sessions_to_main(
                    extra_paths=[processing_log_path(self.repo_dir)],
                    message="umx: sync sessions and processing",
                ):
                    return DreamResult(
                        status="error",
                        added=result.added,
                        pruned=result.pruned,
                        findings=findings,
                        message="failed to sync sessions and processing",
                        pr_proposal=pr_proposal,
                        lint=lint_status,
                    )
                return result

            # Local mode (default): direct write
            final_facts, pruned = self.prune(consolidated, now)
            lock.heartbeat()
            if self._gathered_session_ids:
                mark_sessions_gathered(self.repo_dir, self._gathered_session_ids)
            mark_dream_complete(self.repo_dir, now)
            archive_result = scheduled_archive_sessions(
                self.repo_dir,
                now=now,
                config=self.config,
            )
            archived_sessions = int(archive_result.get("archived_sessions", 0))
            demoted = getattr(self, "_demoted_count", 0)
            msg = f"{len(final_facts)} facts retained"
            if provider_summary:
                msg += f", extraction: {provider_summary}"
            if demoted:
                msg += f", {demoted} demoted"
            if archived_sessions:
                msg += f", {archived_sessions} archived"
            result = DreamResult(
                status="ok",
                added=len(self.new_fact_ids),
                pruned=pruned,
                findings=findings,
                message=msg,
                lint=lint_status,
            )
            complete_processing_run(
                self.repo_dir,
                processing_run_id,
                mode=mode,
                branch=run_branch,
                added=result.added,
                pruned=result.pruned,
                message=result.message,
                dream_provider=getattr(self, "_dream_provider", None),
                dream_partial=getattr(self, "_dream_partial", False),
            )
            commit_result = git_add_and_commit(
                self.repo_dir,
                message="umx: dream cycle",
                config=self.config,
            )
            if commit_result.failed:
                raise RuntimeError(git_commit_failure_message(commit_result, context="commit failed"))
            return result
        except Exception as exc:
            if processing_run_id is not None:
                fail_processing_run(
                    self.repo_dir,
                    processing_run_id,
                    mode=mode,
                    branch=run_branch,
                    error=str(exc),
                )
                if mode in {"remote", "hybrid"}:
                    synced = self._sync_processing_log_to_main("umx: dream processing failed") if self.config.org else True
                    if self.config.org and not synced:
                        logger.error("failed to publish dream processing failure state")
            raise
        finally:
            lock.release()
