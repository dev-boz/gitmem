"""GitHub operations via the `gh` CLI.

All remote GitHub interaction (repo creation, PR management, workflow deployment)
is routed through this module. Requires `gh` to be installed and authenticated.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from umx.governance import GOVERNANCE_LABEL_SPECS

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GitHubRepoRef:
    owner: str | None
    name: str
    url: str | None = None


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


def _parse_github_repo_url(url: str) -> tuple[str | None, str | None]:
    stripped = url.strip()
    if not stripped:
        return None, None

    path: str | None = None
    parsed = urlparse(stripped)
    if parsed.scheme in {"https", "http"}:
        if parsed.hostname != "github.com":
            return None, None
        path = parsed.path.lstrip("/")
    elif parsed.scheme == "ssh":
        parsed = urlparse(stripped)
        if parsed.hostname != "github.com":
            return None, None
        path = parsed.path.lstrip("/")
    elif stripped.startswith("git@github.com:"):
        path = stripped.split(":", 1)[1]
    else:
        return None, None

    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None, None
    owner = parts[-2]
    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return owner or None, name or None


def is_github_repo_url(url: str | None) -> bool:
    if not url:
        return False
    owner, name = _parse_github_repo_url(url)
    return owner is not None and name is not None


def redact_url_credentials(url: str | None) -> str | None:
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def resolve_repo_ref(repo_dir: Path, *, config_org: str | None = None) -> GitHubRepoRef:
    """Resolve the GitHub repo identity for ``repo_dir``.

    Prefer parsing a GitHub ``origin`` URL when available. If that fails,
    preserve the local user repo naming split by mapping ``~/.umx/user`` to
    ``umx-user`` and otherwise fall back to the local repo directory name.
    """
    from umx.git_ops import git_remote_url
    from umx.scope import user_memory_dir

    remote = git_remote_url(repo_dir)
    owner = config_org
    name: str | None = None
    if remote:
        remote_owner, remote_name = _parse_github_repo_url(remote)
        if remote_name:
            owner = remote_owner or config_org
            name = remote_name

    if not name:
        if repo_dir.resolve() == user_memory_dir().resolve():
            name = "umx-user"
        else:
            name = repo_dir.name

    return GitHubRepoRef(owner=owner, name=name, url=remote)


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


def ensure_governance_labels(
    org: str,
    repo_name: str,
    labels: list[str] | None = None,
) -> bool:
    """Ensure governance labels exist on the repo."""
    requested = list(dict.fromkeys(labels or list(GOVERNANCE_LABEL_SPECS)))
    ok = True
    for label in requested:
        color, description = GOVERNANCE_LABEL_SPECS.get(
            label,
            ("5319e7", "gitmem governance label"),
        )
        result = _run_gh(
            "label",
            "create",
            label,
            "--repo",
            f"{org}/{repo_name}",
            "--color",
            color,
            "--description",
            description,
            "--force",
            check=False,
        )
        ok = ok and result.returncode == 0
    return ok


def add_pr_labels(org: str, repo_name: str, pr_number: int, labels: list[str]) -> bool:
    """Add labels to an existing PR."""
    if not labels:
        return True
    ensure_governance_labels(org, repo_name, labels)
    args = ["pr", "edit", str(pr_number), "--repo", f"{org}/{repo_name}"]
    for label in labels:
        args.extend(["--add-label", label])
    result = _run_gh(*args, check=False)
    return result.returncode == 0


def create_pr(
    org: str,
    repo_name: str,
    branch: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> int | None:
    """Open a PR on GitHub. Returns the PR number or None on failure."""
    if labels:
        ensure_governance_labels(org, repo_name, labels)
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


def merge_pr(
    org: str,
    repo_name: str,
    pr_number: int,
    method: str = "squash",
    *,
    match_head_commit: str | None = None,
) -> bool:
    """Merge a PR. Returns True on success."""
    args = [
        "pr", "merge", str(pr_number),
        "--repo", f"{org}/{repo_name}",
        f"--{method}",
        "--delete-branch",
    ]
    if match_head_commit:
        args.extend(["--match-head-commit", match_head_commit])
    result = _run_gh(
        *args,
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
