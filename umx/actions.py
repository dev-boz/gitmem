from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from umx.governance import (
    GOVERNED_FACT_FILES,
    GOVERNED_FACT_PREFIXES,
    GOVERNANCE_PR_BRANCH_PREFIXES,
    GOVERNANCE_REVIEW_TRIGGER_LABELS,
    LABEL_STATE_APPROVED,
)


CHECKOUT_ACTION = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"  # v4.2.2
WORKFLOW_INSTALL_COMMAND = "python -m pip install ."
L1_WORKFLOW_NAME = "l1-dream.yml"
L2_WORKFLOW_NAME = "l2-review.yml"
APPROVAL_GATE_WORKFLOW_NAME = "approval-gate.yml"
MAIN_GUARD_WORKFLOW_NAME = "main-guard.yml"
MAIN_GUARD_REVERT_MARKER = "umx: revert unauthorized main push"
MAIN_GUARD_REVERT_SOURCE_TRAILER = "UMX-Main-Guard-Source"
MAIN_GUARD_REVERT_BASE_TRAILER = "UMX-Main-Guard-Base"
MAIN_GUARD_AUDIT_EVENT = "governance_auto_revert"
MAIN_GUARD_AUDIT_LOG_PATH = "meta/processing.jsonl"


def _indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else "" for line in text.splitlines())


def _governance_pr_detection_python(
    *,
    write_output: bool = False,
    skip_message: str | None = None,
    exit_when_false: bool = False,
) -> str:
    script = dedent(
        """\
        import json
        import os
        import urllib.request

        labels = set(json.loads(os.environ["PR_LABELS_JSON"]))
        trigger_labels = set(json.loads(os.environ["GOVERNANCE_TRIGGER_LABELS"]))
        branch_prefixes = tuple(json.loads(os.environ["GOVERNANCE_BRANCH_PREFIXES"]))
        governed_files = set(json.loads(os.environ["GOVERNED_FACT_FILES"]))
        governed_prefixes = tuple(json.loads(os.environ["GOVERNED_FACT_PREFIXES"]))
        head_ref = os.environ.get("PR_HEAD_REF", "")
        api_url = os.environ["GITHUB_API_URL"].rstrip("/")
        repo = os.environ["GITHUB_REPOSITORY"]
        pr_number = os.environ["PR_NUMBER"]
        token = os.environ["GH_TOKEN"]
        changed_files = []
        page = 1
        while True:
            request = urllib.request.Request(
                f"{api_url}/repos/{repo}/pulls/{pr_number}/files?per_page=100&page={page}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8") or "[]")
            if not isinstance(payload, list):
                raise SystemExit("unexpected PR files payload from GitHub API")
            if not payload:
                break
            for item in payload:
                if not isinstance(item, dict):
                    raise SystemExit("malformed PR files payload from GitHub API")
                filename = item.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise SystemExit("malformed PR files payload from GitHub API")
                changed_files.append(filename)
                if item.get("status") == "renamed":
                    previous = item.get("previous_filename")
                    if not isinstance(previous, str) or not previous:
                        raise SystemExit("malformed PR files payload from GitHub API")
                    changed_files.append(previous)
            if len(payload) < 100:
                break
            page += 1
        governed_paths = [
            path
            for path in changed_files
            if path in governed_files or path.startswith(governed_prefixes)
        ]
        is_governance = (
            head_ref.startswith(branch_prefixes)
            or bool(labels & trigger_labels)
            or bool(governed_paths)
        )
        """
    ).rstrip()
    extras: list[str] = []
    if write_output:
        extras.append(
            dedent(
                """\
                output_path = os.environ.get("GITHUB_OUTPUT")
                if not output_path:
                    raise SystemExit("GITHUB_OUTPUT is required")
                with open(output_path, "a", encoding="utf-8") as handle:
                    handle.write(f"is_governance={'true' if is_governance else 'false'}\\n")
                """
            ).rstrip()
        )
    if skip_message is not None:
        lines = [
            "if not is_governance:",
            f"    print({skip_message!r})",
        ]
        if exit_when_false:
            lines.append("    raise SystemExit(0)")
        extras.append("\n".join(lines))
    if extras:
        script += "\n\n" + "\n\n".join(extras)
    return script


def _replace_governance_pull_request_placeholders(template: str) -> str:
    return template.replace(
        "__GOVERNANCE_TRIGGER_LABELS__", json.dumps(sorted(GOVERNANCE_REVIEW_TRIGGER_LABELS))
    ).replace(
        "__GOVERNANCE_BRANCH_PREFIXES__", json.dumps(list(GOVERNANCE_PR_BRANCH_PREFIXES))
    ).replace(
        "__GOVERNED_FACT_FILES__", json.dumps(sorted(GOVERNED_FACT_FILES))
    ).replace(
        "__GOVERNED_FACT_PREFIXES__", json.dumps(list(GOVERNED_FACT_PREFIXES))
    )


L1_WORKFLOW_TEMPLATE = dedent(
    """\
    name: L1 Dream

    on:
      push:
        paths:
          - sessions/**
      schedule:
        - cron: '0 2 * * *'

    permissions:
      contents: write
      issues: write
      pull-requests: write

    concurrency:
      group: dream-l1-${{ github.ref }}
      cancel-in-progress: true

    jobs:
      dream:
        runs-on: ubuntu-latest
        steps:
          - uses: __CHECKOUT_ACTION__
          - name: Install gitmem
            run: __WORKFLOW_INSTALL_COMMAND__
          - name: Run L1 dream
            run: umx dream --mode remote --tier l1
            env:
              UMX_PROVIDER: groq
              GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
    """
).replace("__CHECKOUT_ACTION__", CHECKOUT_ACTION).replace("__WORKFLOW_INSTALL_COMMAND__", WORKFLOW_INSTALL_COMMAND)


L2_WORKFLOW_TEMPLATE = dedent(
    """\
    name: L2 Review

    on:
      pull_request:
        types:
          - opened
          - synchronize
          - reopened
          - labeled
        branches:
          - main

    permissions:
      contents: write
      issues: write
      pull-requests: write

    concurrency:
      group: dream-l2-${{ github.event.pull_request.number }}
      cancel-in-progress: true

    jobs:
      review:
        runs-on: ubuntu-latest
        steps:
          - name: Detect governance PR
            id: detect_governance
            env:
              GH_TOKEN: ${{ github.token }}
              GITHUB_API_URL: ${{ github.api_url }}
              GITHUB_REPOSITORY: ${{ github.repository }}
              GOVERNANCE_TRIGGER_LABELS: '__GOVERNANCE_TRIGGER_LABELS__'
              GOVERNANCE_BRANCH_PREFIXES: '__GOVERNANCE_BRANCH_PREFIXES__'
              GOVERNED_FACT_FILES: '__GOVERNED_FACT_FILES__'
              GOVERNED_FACT_PREFIXES: '__GOVERNED_FACT_PREFIXES__'
              PR_LABELS_JSON: ${{ toJson(github.event.pull_request.labels.*.name) }}
              PR_HEAD_REF: ${{ github.event.pull_request.head.ref }}
              PR_NUMBER: ${{ github.event.pull_request.number }}
            run: |
              python - <<'PY'
__L2_GOVERNANCE_DETECTION_PY__
              PY
          - uses: __CHECKOUT_ACTION__
            if: steps.detect_governance.outputs.is_governance == 'true'
            with:
              fetch-depth: 0
              ref: ${{ github.event.pull_request.head.sha }}
          - name: Prepare PR head branch
            if: steps.detect_governance.outputs.is_governance == 'true'
            env:
              PR_HEAD_REF: ${{ github.event.pull_request.head.ref }}
              PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
            run: git checkout -B "$PR_HEAD_REF" "$PR_HEAD_SHA"
          - name: Install gitmem
            if: steps.detect_governance.outputs.is_governance == 'true'
            run: __WORKFLOW_INSTALL_COMMAND__
          - name: Run L2 review
            if: steps.detect_governance.outputs.is_governance == 'true'
            run: umx dream --mode remote --tier l2 --pr ${{ github.event.pull_request.number }} --head-sha ${{ github.event.pull_request.head.sha }} --provider nvidia
            env:
              GH_TOKEN: ${{ github.token }}
              NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}
      """
).replace(
    "__L2_GOVERNANCE_DETECTION_PY__",
    _indent_block(
        _governance_pr_detection_python(
            write_output=True,
            skip_message="non-governance PR; L2 review skipped",
        ),
        14,
    ),
).replace(
    "__CHECKOUT_ACTION__", CHECKOUT_ACTION
).replace(
    "__WORKFLOW_INSTALL_COMMAND__", WORKFLOW_INSTALL_COMMAND
)
L2_WORKFLOW_TEMPLATE = _replace_governance_pull_request_placeholders(L2_WORKFLOW_TEMPLATE)
L2_WORKFLOW_TEMPLATE = dedent(L2_WORKFLOW_TEMPLATE)


APPROVAL_GATE_WORKFLOW_TEMPLATE = dedent(
    """\
    name: Governance Approval Gate

    on:
      pull_request:
        types:
          - opened
          - synchronize
          - labeled
          - unlabeled
        branches:
          - main

    permissions:
      contents: read
      pull-requests: read

    concurrency:
      group: governance-approval-${{ github.event.pull_request.number }}
      cancel-in-progress: true

    jobs:
      approval-gate:
        runs-on: ubuntu-latest
        steps:
          - name: Enforce governance approval gate
            env:
              GH_TOKEN: ${{ github.token }}
              GITHUB_API_URL: ${{ github.api_url }}
              GITHUB_REPOSITORY: ${{ github.repository }}
              GOVERNANCE_TRIGGER_LABELS: '__GOVERNANCE_TRIGGER_LABELS__'
              GOVERNANCE_BRANCH_PREFIXES: '__GOVERNANCE_BRANCH_PREFIXES__'
              GOVERNED_FACT_FILES: '__GOVERNED_FACT_FILES__'
              GOVERNED_FACT_PREFIXES: '__GOVERNED_FACT_PREFIXES__'
              PR_LABELS_JSON: ${{ toJson(github.event.pull_request.labels.*.name) }}
              PR_HEAD_REF: ${{ github.event.pull_request.head.ref }}
              PR_NUMBER: ${{ github.event.pull_request.number }}
            run: |
              python - <<'PY'
__GOVERNANCE_DETECTION_PY__
              required = "__APPROVED_LABEL__"
              if required not in labels:
                  print(f"governance PR missing required label: {required}")
                  raise SystemExit(1)
              print("governance approval gate satisfied")
              PY
    """
).replace(
    "__GOVERNANCE_DETECTION_PY__",
    _indent_block(
        _governance_pr_detection_python(
            skip_message="non-governance PR; approval gate skipped",
            exit_when_false=True,
        ),
        14,
    ),
).replace(
    "__APPROVED_LABEL__", LABEL_STATE_APPROVED
)
APPROVAL_GATE_WORKFLOW_TEMPLATE = _replace_governance_pull_request_placeholders(APPROVAL_GATE_WORKFLOW_TEMPLATE)
APPROVAL_GATE_WORKFLOW_TEMPLATE = dedent(APPROVAL_GATE_WORKFLOW_TEMPLATE)


def _main_guard_workflow_template(mode: str = "remote") -> str:
    if mode not in {"remote", "hybrid"}:
        raise ValueError(f"unsupported governance mode for main guard workflow: {mode}")
    return dedent(
        """\
        name: Governance Main Guard

        on:
          push:
            branches:
              - main

        permissions:
          contents: write
          pull-requests: read

        concurrency:
          group: governance-main-guard-${{ github.ref }}
          cancel-in-progress: false

        jobs:
          main-guard:
            runs-on: ubuntu-latest
            steps:
              - uses: __CHECKOUT_ACTION__
                with:
                  fetch-depth: 0
              - name: Revert unauthorized governed pushes to main
                env:
                  GH_TOKEN: ${{ github.token }}
                  GITHUB_API_URL: ${{ github.api_url }}
                  GITHUB_REPOSITORY: ${{ github.repository }}
                  PUSH_BEFORE: ${{ github.event.before }}
                  PUSH_AFTER: ${{ github.sha }}
                  GOVERNANCE_MODE: '__GOVERNANCE_MODE__'
                  REQUIRED_APPROVAL_LABEL: '__APPROVED_LABEL__'
                  GOVERNED_FACT_FILES: '__GOVERNED_FACT_FILES__'
                  GOVERNED_FACT_PREFIXES: '__GOVERNED_FACT_PREFIXES__'
                  PROCESSING_LOG_PATH: '__PROCESSING_LOG_PATH__'
                  AUDIT_EVENT: '__AUDIT_EVENT__'
                  REVERT_MARKER: '__REVERT_MARKER__'
                  REVERT_SOURCE_TRAILER: '__REVERT_SOURCE_TRAILER__'
                  REVERT_BASE_TRAILER: '__REVERT_BASE_TRAILER__'
                run: |
                  python - <<'PY'
                  import json
                  import os
                  import subprocess
                  import urllib.request
                  from datetime import datetime, timezone
                  from pathlib import Path

                  ZERO = "0" * 40


                  def now_z() -> str:
                      return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


                  def run_git(*args: str, check: bool = True) -> str:
                      result = subprocess.run(
                          ["git", *args],
                          capture_output=True,
                          text=True,
                          check=False,
                      )
                      if check and result.returncode != 0:
                          raise SystemExit(
                              (result.stderr or result.stdout or f"git {' '.join(args)} failed").strip()
                          )
                      return result.stdout.strip()


                  def git_tree(rev: str):
                      result = subprocess.run(
                          ["git", "rev-parse", f"{rev}^{{tree}}"],
                          capture_output=True,
                          text=True,
                          check=False,
                      )
                      if result.returncode != 0:
                          return None
                      return result.stdout.strip() or None


                  def commit_governed_paths(commit: str):
                      parent_parts = run_git("rev-list", "--parents", "-n", "1", commit).split()
                      args = ["diff-tree", "--no-commit-id", "--name-only", "-r"]
                      if len(parent_parts) > 2:
                          args.append("-m")
                      args.append(commit)
                      changed = [
                          line.strip()
                          for line in run_git(*args).splitlines()
                          if line.strip()
                      ]
                      return [
                          path
                          for path in changed
                          if path in governed_files or path.startswith(governed_prefixes)
                      ]


                  def load_commit_pulls(commit: str):
                      request = urllib.request.Request(
                          f"{api_url}/repos/{repo}/commits/{commit}/pulls",
                          headers={
                              "Accept": "application/vnd.github+json",
                              "Authorization": f"Bearer {token}",
                          },
                      )
                      try:
                          with urllib.request.urlopen(request, timeout=30) as response:
                              payload = json.loads(response.read().decode("utf-8") or "[]")
                      except Exception as exc:  # noqa: BLE001
                          raise SystemExit(f"failed to resolve commit-associated PRs for {commit}: {exc}") from exc
                      if not isinstance(payload, list):
                          raise SystemExit("unexpected commit pull request payload from GitHub API")
                      return payload


                  def find_approved_pr(payload):
                      associated = []
                      for item in payload:
                          if not isinstance(item, dict):
                              raise SystemExit("malformed commit pull request payload from GitHub API")
                          number = item.get("number")
                          if isinstance(number, int):
                              associated.append(number)
                          if item.get("merged_at") is None:
                              continue
                          base = item.get("base")
                          if not isinstance(base, dict) or base.get("ref") != "main":
                              continue
                          labels = item.get("labels")
                          if not isinstance(labels, list):
                              continue
                          label_names = {
                              label.get("name")
                              for label in labels
                              if isinstance(label, dict) and isinstance(label.get("name"), str)
                          }
                          if required_label in label_names:
                              return item, associated
                      return None, associated


                  api_url = os.environ["GITHUB_API_URL"].rstrip("/")
                  repo = os.environ["GITHUB_REPOSITORY"]
                  token = os.environ["GH_TOKEN"]
                  before = os.environ["PUSH_BEFORE"]
                  after = os.environ["PUSH_AFTER"]
                  guard_mode = os.environ["GOVERNANCE_MODE"]
                  required_label = os.environ["REQUIRED_APPROVAL_LABEL"]
                  governed_files = set(json.loads(os.environ["GOVERNED_FACT_FILES"]))
                  governed_prefixes = tuple(json.loads(os.environ["GOVERNED_FACT_PREFIXES"]))
                  processing_log_path = Path(os.environ["PROCESSING_LOG_PATH"])
                  audit_event = os.environ["AUDIT_EVENT"]
                  revert_marker = os.environ["REVERT_MARKER"]
                  revert_source_trailer = os.environ["REVERT_SOURCE_TRAILER"]
                  revert_base_trailer = os.environ["REVERT_BASE_TRAILER"]

                  if not after or before == ZERO:
                      print("initial main bootstrap; main guard skipped")
                      raise SystemExit(0)

                  head_message = run_git("log", "-1", "--pretty=%B", after)
                  if head_message.startswith(revert_marker):
                      revert_source = None
                      revert_base = None
                      for line in head_message.splitlines():
                          if line.startswith(f"{revert_source_trailer}:"):
                              revert_source = line.split(":", 1)[1].strip() or None
                              continue
                          if line.startswith(f"{revert_base_trailer}:"):
                              revert_base = line.split(":", 1)[1].strip() or None
                              break
                      restored_tree = git_tree(after)
                      revert_base_tree = git_tree(revert_base) if revert_base else None
                      if (
                          revert_source == before
                          and restored_tree is not None
                          and restored_tree == revert_base_tree
                      ):
                          print("main guard revert commit detected; skipping")
                          raise SystemExit(0)

                  changed_paths = [
                      line.strip()
                      for line in run_git("diff", "--name-only", f"{before}..{after}").splitlines()
                      if line.strip()
                  ]
                  governed_paths = [
                      path
                      for path in changed_paths
                      if path in governed_files or path.startswith(governed_prefixes)
                  ]
                  if not governed_paths:
                      print("no governed paths changed on main; guard skipped")
                      raise SystemExit(0)

                  commits = [
                      line.strip()
                      for line in run_git("rev-list", f"{before}..{after}").splitlines()
                      if line.strip()
                  ]
                  if not commits:
                      print("no commits to revert")
                      raise SystemExit(0)

                  associated_pr_numbers = []
                  approved_pr_numbers = []
                  unauthorized_commits = []
                  unauthorized_governed_paths = []
                  for commit in commits:
                      commit_paths = commit_governed_paths(commit)
                      if not commit_paths:
                          continue
                      approved_pr, commit_pr_numbers = find_approved_pr(load_commit_pulls(commit))
                      associated_pr_numbers.extend(commit_pr_numbers)
                      if approved_pr is not None:
                          approved_number = approved_pr.get("number")
                          if isinstance(approved_number, int):
                              approved_pr_numbers.append(approved_number)
                          continue
                      unauthorized_commits.append(commit)
                      unauthorized_governed_paths.extend(commit_paths)
                  associated_pr_numbers = list(dict.fromkeys(associated_pr_numbers))
                  approved_pr_numbers = list(dict.fromkeys(approved_pr_numbers))
                  unauthorized_governed_paths = sorted(dict.fromkeys(unauthorized_governed_paths))
                  if not unauthorized_commits:
                      if approved_pr_numbers:
                          approved_summary = ", ".join(f"#{number}" for number in approved_pr_numbers)
                          print(f"all governed commits matched approved PRs: {approved_summary}")
                      else:
                          print("no unauthorized governed commits found")
                      raise SystemExit(0)

                  run_git("config", "user.name", "github-actions[bot]")
                  run_git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
                  for commit in unauthorized_commits:
                      parent_parts = run_git("rev-list", "--parents", "-n", "1", commit).split()
                      args = ["revert", "--no-edit", "--no-commit"]
                      if len(parent_parts) > 2:
                          args.extend(["-m", "1"])
                      args.append(commit)
                      try:
                          run_git(*args)
                      except SystemExit:
                          run_git("revert", "--abort", check=False)
                          raise

                  processing_log_path.parent.mkdir(parents=True, exist_ok=True)
                  audit_record = {
                      "run_id": f"main-guard-{after[:12]}",
                      "event": audit_event,
                      "status": "completed",
                      "ts": now_z(),
                      "actor": "github-actions",
                      "mode": guard_mode,
                      "branch": "main",
                      "repo": repo,
                      "before": before,
                      "after": after,
                      "required_label": required_label,
                      "associated_pr_numbers": associated_pr_numbers,
                      "approved_pr_numbers": approved_pr_numbers,
                      "governed_paths": unauthorized_governed_paths,
                      "reverted_commits": unauthorized_commits,
                  }
                  github_run_id = os.environ.get("GITHUB_RUN_ID")
                  if github_run_id:
                      audit_record["github_run_id"] = github_run_id
                  with processing_log_path.open("a", encoding="utf-8") as handle:
                      handle.write(json.dumps(audit_record, sort_keys=True) + "\\n")

                  summary = ", ".join(unauthorized_governed_paths[:10])
                  if len(unauthorized_governed_paths) > 10:
                      summary = f"{summary}, ... (+{len(unauthorized_governed_paths) - 10} more)"
                  run_git(
                      "commit",
                      "-m",
                      f"{revert_marker} {after[:12]}",
                      "-m",
                      (
                          "Reason: governed fact state reached main without a merged approved PR.\\n"
                          f"Paths: {summary}\\n\\n"
                          f"{revert_source_trailer}: {after}\\n"
                          f"{revert_base_trailer}: {before}"
                      ),
                  )
                  run_git("push", "origin", "HEAD:main")
                  print("unauthorized governed main push reverted")
                  PY
        """
    ).replace("__CHECKOUT_ACTION__", CHECKOUT_ACTION).replace(
        "__GOVERNED_FACT_FILES__", json.dumps(sorted(GOVERNED_FACT_FILES))
    ).replace(
        "__GOVERNED_FACT_PREFIXES__", json.dumps(list(GOVERNED_FACT_PREFIXES))
    ).replace(
        "__GOVERNANCE_MODE__", mode
    ).replace(
        "__PROCESSING_LOG_PATH__", MAIN_GUARD_AUDIT_LOG_PATH
    ).replace(
        "__AUDIT_EVENT__", MAIN_GUARD_AUDIT_EVENT
    ).replace(
        "__APPROVED_LABEL__", LABEL_STATE_APPROVED
    ).replace(
        "__REVERT_MARKER__", MAIN_GUARD_REVERT_MARKER
    ).replace(
        "__REVERT_SOURCE_TRAILER__", MAIN_GUARD_REVERT_SOURCE_TRAILER
    ).replace(
        "__REVERT_BASE_TRAILER__", MAIN_GUARD_REVERT_BASE_TRAILER
    )


MAIN_GUARD_WORKFLOW_TEMPLATE = _main_guard_workflow_template()


def workflow_templates(mode: str = "remote") -> dict[str, str]:
    return {
        L1_WORKFLOW_NAME: L1_WORKFLOW_TEMPLATE,
        L2_WORKFLOW_NAME: L2_WORKFLOW_TEMPLATE,
        APPROVAL_GATE_WORKFLOW_NAME: APPROVAL_GATE_WORKFLOW_TEMPLATE,
        MAIN_GUARD_WORKFLOW_NAME: _main_guard_workflow_template(mode),
    }


def write_workflow_templates(repo_root: Path, *, mode: str = "remote") -> list[Path]:
    workflows_dir = repo_root / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, template in workflow_templates(mode).items():
        path = workflows_dir / filename
        path.write_text(template)
        written.append(path)
    return written
