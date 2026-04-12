from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GITIGNORE_ENTRIES = [
    "local/",
    "meta/*.sqlite",
    "meta/*.json",
    "meta/*.jsonl",
    "!meta/tombstones.jsonl",
    "meta/dream.lock",
    ".umx.json",
    "__pycache__/",
]


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
    _run_git(repo_dir, "add", ".gitignore")
    _run_git(repo_dir, "commit", "-m", "umx: initial commit")


def git_add_and_commit(
    repo_dir: Path,
    paths: list[Path] | None = None,
    message: str = "umx: update facts",
) -> bool:
    """Stage changed files and commit.

    If paths is None, stage all tracked changes.
    Returns True if a commit was made, False if nothing to commit.
    """
    if not is_git_repo(repo_dir):
        return False

    if paths is not None:
        for p in paths:
            try:
                rel = p.relative_to(repo_dir)
            except ValueError:
                rel = p
            _run_git(repo_dir, "add", "--force", str(rel))
    else:
        _run_git(repo_dir, "add", "-A")

    status = _run_git(repo_dir, "diff", "--cached", "--quiet")
    if status.returncode == 0:
        return False

    result = _run_git(repo_dir, "commit", "-m", message)
    if result.returncode != 0:
        logger.warning("git commit failed: %s", result.stderr.strip())
        return False
    return True


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
    result = _run_git(repo_dir, "fetch", "origin")
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


def git_path_exists_at_ref(repo_dir: Path, ref: str, path: str) -> bool:
    """Return True when ``path`` exists at ``ref``."""
    result = _run_git(repo_dir, "cat-file", "-e", f"{ref}:{path}")
    return result.returncode == 0


def git_restore_path(repo_dir: Path, ref: str, path: str) -> bool:
    """Restore one path from ``ref`` into the working tree."""
    result = _run_git(repo_dir, "checkout", ref, "--", path)
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
    return len(files) if committed else 0
