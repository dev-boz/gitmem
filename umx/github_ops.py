"""GitHub operations via the `gh` CLI.

All remote GitHub interaction (repo creation, PR management, workflow deployment)
is routed through this module. Requires `gh` to be installed and authenticated.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Raised when a gh CLI operation fails."""


def _run_gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise GitHubError("gh CLI is not installed; install from https://cli.github.com/")
    if check and result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result


def gh_available() -> bool:
    """Check if gh CLI is installed and authenticated."""
    try:
        result = _run_gh("auth", "status", check=False)
        return result.returncode == 0
    except GitHubError:
        return False


def repo_exists(org: str, name: str) -> bool:
    """Check if a repo exists under the org."""
    result = _run_gh("repo", "view", f"{org}/{name}", "--json", "name", check=False)
    return result.returncode == 0


def create_repo(org: str, name: str, private: bool = True) -> str:
    """Create a repo under the org. Returns the clone URL."""
    visibility = "--private" if private else "--public"
    result = _run_gh(
        "repo", "create", f"{org}/{name}",
        visibility,
        "--clone=false",
        "--description", f"umx memory repo for {name}",
    )
    url = f"https://github.com/{org}/{name}.git"
    logger.info("created repo %s/%s", org, name)
    return url


def ensure_repo(org: str, name: str, private: bool = True) -> str:
    """Create repo if it doesn't exist. Returns the clone URL."""
    if repo_exists(org, name):
        return f"https://github.com/{org}/{name}.git"
    return create_repo(org, name, private=private)


def set_remote(repo_dir: Path, url: str, remote: str = "origin") -> None:
    """Set (or update) the git remote origin for a local repo."""
    from umx.git_ops import _run_git
    existing = _run_git(repo_dir, "remote", "get-url", remote)
    if existing.returncode == 0:
        if existing.stdout.strip() == url:
            return
        _run_git(repo_dir, "remote", "set-url", remote, url)
    else:
        _run_git(repo_dir, "remote", "add", remote, url)


def push_branch(repo_dir: Path, branch: str, set_upstream: bool = True) -> bool:
    """Push a branch to origin."""
    from umx.git_ops import git_push
    return git_push(repo_dir, branch=branch, set_upstream=set_upstream)


def push_main(repo_dir: Path) -> bool:
    """Push main to origin (for session sync)."""
    from umx.git_ops import git_push
    return git_push(repo_dir, branch="main")


def create_pr(
    org: str,
    repo_name: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> int | None:
    """Open a PR on GitHub. Returns the PR number or None on failure."""
    args = [
        "pr", "create",
        "--repo", f"{org}/{repo_name}",
        "--head", branch,
        "--base", "main",
        "--title", title,
        "--body", body,
    ]
    if labels:
        for label in labels:
            args.extend(["--label", label])
    result = _run_gh(*args, check=False)
    if result.returncode != 0:
        # Labels may not exist yet; retry without labels
        if labels and "label" in result.stderr.lower():
            args_no_labels = [a for i, a in enumerate(args) if a != "--label" and (i == 0 or args[i - 1] != "--label")]
            result = _run_gh(*args_no_labels, check=False)
        if result.returncode != 0:
            logger.warning("PR creation failed: %s", result.stderr.strip())
            return None
    # gh pr create prints the URL; extract PR number from it
    url = result.stdout.strip()
    if "/" in url:
        try:
            return int(url.rstrip("/").split("/")[-1])
        except ValueError:
            pass
    return None


def merge_pr(org: str, repo_name: str, pr_number: int, method: str = "squash") -> bool:
    """Merge a PR. Returns True on success."""
    result = _run_gh(
        "pr", "merge", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        f"--{method}",
        "--delete-branch",
        check=False,
    )
    return result.returncode == 0


def comment_pr(org: str, repo_name: str, pr_number: int, body: str) -> bool:
    """Add a comment to a PR."""
    result = _run_gh(
        "pr", "comment", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        "--body", body,
        check=False,
    )
    return result.returncode == 0


def close_pr(org: str, repo_name: str, pr_number: int, comment: str | None = None) -> bool:
    """Close a PR without merging."""
    if comment:
        comment_pr(org, repo_name, pr_number, comment)
    result = _run_gh(
        "pr", "close", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        check=False,
    )
    return result.returncode == 0


def deploy_workflows(repo_dir: Path) -> list[Path]:
    """Write GitHub Actions workflow templates to the repo."""
    from umx.actions import write_workflow_templates
    return write_workflow_templates(repo_dir)
