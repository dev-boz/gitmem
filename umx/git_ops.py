from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from umx.config import UMXConfig, load_config
from umx.scope import config_path

logger = logging.getLogger(__name__)

_GITIGNORE_ENTRIES = [
    "local/",
    "meta/*.sqlite",
    "meta/*.json",
    "meta/*.jsonl",
    "!meta/tombstones.jsonl",
    "!meta/processing.jsonl",
    "meta/dream.lock",
    ".umx.json",
    "__pycache__/",
]


@dataclass(slots=True, frozen=True)
class GitCommitResult:
    status: Literal["noop", "committed", "failed"]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    signed: bool = False

    @property
    def committed(self) -> bool:
        return self.status == "committed"

    @property
    def noop(self) -> bool:
        return self.status == "noop"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def __bool__(self) -> bool:
        return self.committed

    @classmethod
    def noop_result(cls) -> GitCommitResult:
        return cls(status="noop")

    @classmethod
    def committed_result(
        cls,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        signed: bool = False,
    ) -> GitCommitResult:
        return cls(
            status="committed",
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            signed=signed,
        )

    @classmethod
    def failed_result(
        cls,
        *,
        returncode: int = 1,
        stdout: str = "",
        stderr: str = "",
        signed: bool = False,
    ) -> GitCommitResult:
        return cls(
            status="failed",
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            signed=signed,
        )


class GitCommitError(RuntimeError):
    def __init__(self, message: str, result: GitCommitResult) -> None:
        super().__init__(message)
        self.result = result


def _run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            check=check,
        )
    except FileNotFoundError:
        logger.warning("git is not installed; skipping git operation")
        return subprocess.CompletedProcess(args=["git"], returncode=128, stdout="", stderr="git not found")


def _resolve_commit_signing(config: UMXConfig | None = None) -> tuple[bool, bool]:
    resolved = config
    if resolved is None:
        resolved = load_config(config_path())
    sign_commits = bool(resolved.git.sign_commits or resolved.git.require_signed_commits)
    require_signed_commits = bool(resolved.git.require_signed_commits)
    return sign_commits, require_signed_commits


def git_signing_payload(config: UMXConfig | None = None) -> dict[str, bool]:
    resolved = config
    if resolved is None:
        resolved = load_config(config_path())
    sign_commits, require_signed_commits = _resolve_commit_signing(resolved)
    return {
        "enabled": sign_commits,
        "sign_commits": bool(resolved.git.sign_commits),
        "require_signed_commits": require_signed_commits,
    }


def git_commit_failure_message(
    result: GitCommitResult,
    *,
    context: str = "git commit failed",
) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    return f"{context}: {detail}" if detail else context


def raise_for_git_commit_failure(
    result: GitCommitResult,
    *,
    context: str = "git commit failed",
) -> GitCommitResult:
    if result.failed:
        raise GitCommitError(git_commit_failure_message(result, context=context), result)
    return result


def is_git_repo(repo_dir: Path) -> bool:
    """Check if repo_dir is a git repository."""
    if not repo_dir.exists():
        return False
    result = _run_git(repo_dir, "rev-parse", "--git-dir")
    return result.returncode == 0


def git_init(repo_dir: Path) -> None:
    """Initialize a git repo at repo_dir if not already one.

    Create .gitignore with standard umx ignore patterns.
    """
    if is_git_repo(repo_dir):
        return
    repo_dir.mkdir(parents=True, exist_ok=True)
    result = _run_git(repo_dir, "init", "-b", "main")
    if result.returncode != 0:
        logger.warning("git init failed: %s", result.stderr.strip())
        return
    gitignore = repo_dir / ".gitignore"
    gitignore.write_text("\n".join(_GITIGNORE_ENTRIES) + "\n")
    commit_result = git_add_and_commit(repo_dir, paths=[gitignore], message="umx: initial commit")
    raise_for_git_commit_failure(commit_result, context="git init commit failed")


def git_add_and_commit(
    repo_dir: Path,
    paths: list[Path] | None = None,
    message: str = "umx: update facts",
    *,
    config: UMXConfig | None = None,
) -> GitCommitResult:
    """Stage changed files and commit.

    If paths is None, stage all tracked changes.
    Returns a structured result that distinguishes no-op, success, and failure.
    """
    if not is_git_repo(repo_dir):
        return GitCommitResult.failed_result(returncode=128, stderr="not a git repository")

    if paths is not None:
        for p in paths:
            try:
                rel = p.relative_to(repo_dir)
            except ValueError:
                rel = p
            add_result = _run_git(repo_dir, "add", "--force", str(rel))
            if add_result.returncode != 0:
                return GitCommitResult.failed_result(
                    returncode=add_result.returncode,
                    stdout=add_result.stdout,
                    stderr=add_result.stderr,
                )
    else:
        add_result = _run_git(repo_dir, "add", "-A")
        if add_result.returncode != 0:
            return GitCommitResult.failed_result(
                returncode=add_result.returncode,
                stdout=add_result.stdout,
                stderr=add_result.stderr,
            )

    status = _run_git(repo_dir, "diff", "--cached", "--quiet")
    if status.returncode == 0:
        return GitCommitResult.noop_result()
    if status.returncode != 1:
        return GitCommitResult.failed_result(
            returncode=status.returncode,
            stdout=status.stdout,
            stderr=status.stderr,
        )

    sign_commits, require_signed_commits = _resolve_commit_signing(config)
    commit_args = ["commit"]
    if sign_commits:
        commit_args.append("-S")
    commit_args.extend(["-m", message])
    result = _run_git(repo_dir, *commit_args)
    if result.returncode != 0:
        context = "git signed commit failed" if require_signed_commits else "git commit failed"
        logger.warning("%s: %s", context, result.stderr.strip() or result.stdout.strip())
        return GitCommitResult.failed_result(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            signed=sign_commits,
        )
    return GitCommitResult.committed_result(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        signed=sign_commits,
    )


def git_status(repo_dir: Path) -> str:
    """Return ``git status --porcelain`` output."""
    if not is_git_repo(repo_dir):
        return ""
    result = _run_git(repo_dir, "status", "--porcelain", "-uall")
    return result.stdout


def changed_paths(repo_dir: Path, prefix: str | None = None) -> list[Path]:
    """Return changed paths from ``git status --porcelain``.

    When ``prefix`` is provided, only paths under that repository-relative prefix
    are returned.
    """
    output = git_status(repo_dir)
    if not output:
        return []

    seen: set[Path] = set()
    paths: list[Path] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        file_part = line[3:]
        candidates = file_part.split(" -> ") if " -> " in file_part else [file_part]
        for candidate in candidates:
            if prefix is not None and not candidate.startswith(prefix):
                continue
            path = repo_dir / candidate
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def uncommitted_sessions(repo_dir: Path) -> list[Path]:
    """Find session .jsonl files that are not yet committed (safety sweep).

    Uses git status to find untracked/modified files in sessions/.
    """
    return [path for path in changed_paths(repo_dir, prefix="sessions/") if path.suffix == ".jsonl"]


def git_remote_url(repo_dir: Path) -> str | None:
    """Get the remote origin URL."""
    result = _run_git(repo_dir, "remote", "get-url", "origin")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_fetch(repo_dir: Path) -> bool:
    """Fetch from remote."""
    result = _run_git(repo_dir, "fetch", "--prune", "origin")
    return result.returncode == 0


def git_pull_rebase(repo_dir: Path) -> bool:
    """Pull with rebase from remote."""
    result = _run_git(repo_dir, "pull", "--rebase", "origin")
    return result.returncode == 0


def git_push(repo_dir: Path, branch: str = "main", set_upstream: bool = False) -> bool:
    """Push to remote. Returns True on success."""
    args = ["push"]
    if set_upstream:
        args.extend(["--set-upstream", "origin", branch])
    else:
        args.extend(["origin", branch])
    result = _run_git(repo_dir, *args)
    return result.returncode == 0


def git_create_branch(repo_dir: Path, branch: str, checkout: bool = True) -> bool:
    """Create and optionally checkout a branch."""
    if checkout:
        result = _run_git(repo_dir, "checkout", "-b", branch)
    else:
        result = _run_git(repo_dir, "branch", branch)
    return result.returncode == 0


def git_checkout(repo_dir: Path, branch: str) -> bool:
    """Checkout an existing branch."""
    result = _run_git(repo_dir, "checkout", branch)
    return result.returncode == 0


def git_delete_branch(repo_dir: Path, branch: str, *, force: bool = False) -> bool:
    """Delete a local branch."""
    result = _run_git(repo_dir, "branch", "-D" if force else "-d", branch)
    return result.returncode == 0


def git_current_branch(repo_dir: Path) -> str | None:
    """Get current branch name."""
    result = _run_git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_ref_exists(repo_dir: Path, ref: str) -> bool:
    """Return True when a git ref exists."""
    result = _run_git(repo_dir, "rev-parse", "--verify", ref)
    return result.returncode == 0


def git_ref_sha(repo_dir: Path, ref: str) -> str | None:
    """Return the resolved object ID for ``ref`` when it exists."""
    result = _run_git(repo_dir, "rev-parse", "--verify", ref)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_path_exists_at_ref(repo_dir: Path, ref: str, path: str) -> bool:
    """Return True when ``path`` exists at ``ref``."""
    result = _run_git(repo_dir, "cat-file", "-e", f"{ref}:{path}")
    return result.returncode == 0


def git_read_text_at_ref(repo_dir: Path, ref: str, path: str) -> str | None:
    """Return file contents from ``ref`` when available."""
    result = _run_git(repo_dir, "show", f"{ref}:{path}")
    if result.returncode != 0:
        return None
    return result.stdout


def git_read_text_at_ref_strict(repo_dir: Path, ref: str, path: str) -> str:
    """Return file contents from ``ref`` or raise on git failure."""
    result = _run_git(repo_dir, "show", f"{ref}:{path}")
    if result.returncode != 0:
        raise RuntimeError(f"failed to read {path} at {ref}")
    return result.stdout


def git_restore_path(repo_dir: Path, ref: str, path: str) -> bool:
    """Restore one path from ``ref`` into the working tree."""
    result = _run_git(repo_dir, "checkout", ref, "--", path)
    return result.returncode == 0


def git_reset_paths(repo_dir: Path, paths: list[Path]) -> bool:
    """Unstage repository-relative paths from the index."""
    if not paths:
        return True
    relatives: list[str] = []
    for path in paths:
        try:
            relatives.append(path.relative_to(repo_dir).as_posix())
        except ValueError:
            relatives.append(path.as_posix())
    result = _run_git(repo_dir, "reset", "HEAD", "--", *relatives)
    return result.returncode == 0


def diff_paths_against_ref(repo_dir: Path, ref: str, pathspec: str | None = None) -> list[Path]:
    """Return repository paths that differ from ``ref``."""
    args = ["diff", "--name-only", ref]
    if pathspec is not None:
        args.extend(["--", pathspec])
    result = _run_git(repo_dir, *args)
    if result.returncode != 0 or not result.stdout:
        return []
    return [repo_dir / line for line in result.stdout.splitlines() if line.strip()]


def diff_committed_paths_against_ref(
    repo_dir: Path,
    ref: str,
    pathspec: str | None = None,
) -> list[Path]:
    """Return committed branch paths that differ from ``ref``."""
    return diff_committed_paths_between_refs(repo_dir, ref, "HEAD", pathspec=pathspec)


def diff_committed_paths_between_refs(
    repo_dir: Path,
    base_ref: str,
    head_ref: str = "HEAD",
    pathspec: str | None = None,
) -> list[Path]:
    """Return committed paths that differ between ``base_ref`` and ``head_ref``."""
    args = ["diff", "--name-only", f"{base_ref}...{head_ref}"]
    if pathspec is not None:
        args.extend(["--", pathspec])
    result = _run_git(repo_dir, *args)
    if result.returncode != 0 or not result.stdout:
        return []
    return [repo_dir / line for line in result.stdout.splitlines() if line.strip()]


def diff_committed_paths_between_refs_strict(
    repo_dir: Path,
    base_ref: str,
    head_ref: str = "HEAD",
    pathspec: str | None = None,
) -> list[Path]:
    """Return committed paths or raise when git diff fails."""
    args = ["diff", "--name-only", f"{base_ref}...{head_ref}"]
    if pathspec is not None:
        args.extend(["--", pathspec])
    result = _run_git(repo_dir, *args)
    if result.returncode != 0:
        raise RuntimeError(f"failed to diff {base_ref}...{head_ref}")
    return [repo_dir / line for line in result.stdout.splitlines() if line.strip()]


def git_diff_stat(repo_dir: Path, base: str = "main") -> str:
    """Get diff stat against base branch."""
    result = _run_git(repo_dir, "diff", "--stat", base)
    return result.stdout


def git_log_oneline(repo_dir: Path, count: int = 10) -> str:
    """Get recent commits one-line."""
    result = _run_git(repo_dir, "log", "--oneline", f"-{count}")
    return result.stdout


def safety_sweep(repo_dir: Path) -> int:
    """Commit any uncommitted session files from prior crashed runs.

    Returns count of files committed.
    """
    files = uncommitted_sessions(repo_dir)
    if not files:
        return 0
    committed = git_add_and_commit(
        repo_dir,
        paths=files,
        message=f"umx: safety sweep – {len(files)} session(s)",
    )
    raise_for_git_commit_failure(committed, context="git safety sweep failed")
    return len(files) if committed.committed else 0
