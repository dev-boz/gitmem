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
WORKFLOW_INSTALL_COMMAND = 'python -m pip install "git+https://github.com/dev-boz/gitmem.git@main"'
L1_WORKFLOW_NAME = "l1-dream.yml"
L2_WORKFLOW_NAME = "l2-review.yml"
APPROVAL_GATE_WORKFLOW_NAME = "approval-gate.yml"
MAIN_GUARD_WORKFLOW_NAME = "main-guard.yml"
MAIN_GUARD_REVERT_MARKER = "umx: revert unauthorized main push"


def _l2_review_condition() -> str:
    return " ||\n          ".join(
        f"contains(github.event.pull_request.labels.*.name, '{label}')"
        for label in GOVERNANCE_REVIEW_TRIGGER_LABELS
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
        if: >-
          __L2_REVIEW_CONDITION__
        steps:
          - uses: __CHECKOUT_ACTION__
            with:
              fetch-depth: 0
              ref: ${{ github.event.pull_request.head.sha }}
          - name: Prepare PR head branch
            env:
              PR_HEAD_REF: ${{ github.event.pull_request.head.ref }}
              PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
            run: git checkout -B "$PR_HEAD_REF" "$PR_HEAD_SHA"
          - name: Install gitmem
            run: __WORKFLOW_INSTALL_COMMAND__
          - name: Run L2 review
            run: umx dream --mode remote --tier l2 --pr ${{ github.event.pull_request.number }} --head-sha ${{ github.event.pull_request.head.sha }}
            env:
              GH_TOKEN: ${{ github.token }}
              ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    """
).replace("__L2_REVIEW_CONDITION__", _l2_review_condition()).replace("__CHECKOUT_ACTION__", CHECKOUT_ACTION).replace(
    "__WORKFLOW_INSTALL_COMMAND__", WORKFLOW_INSTALL_COMMAND
)


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
              if not is_governance:
                  print("non-governance PR; approval gate skipped")
                  raise SystemExit(0)
              required = "__APPROVED_LABEL__"
              if required not in labels:
                  print(f"governance PR missing required label: {required}")
                  raise SystemExit(1)
              print("governance approval gate satisfied")
              PY
    """
).replace("__GOVERNANCE_TRIGGER_LABELS__", json.dumps(sorted(GOVERNANCE_REVIEW_TRIGGER_LABELS))).replace(
    "__GOVERNANCE_BRANCH_PREFIXES__", json.dumps(list(GOVERNANCE_PR_BRANCH_PREFIXES))
).replace(
    "__GOVERNED_FACT_FILES__", json.dumps(sorted(GOVERNED_FACT_FILES))
).replace(
    "__GOVERNED_FACT_PREFIXES__", json.dumps(list(GOVERNED_FACT_PREFIXES))
).replace(
    "__APPROVED_LABEL__", LABEL_STATE_APPROVED
)


MAIN_GUARD_WORKFLOW_TEMPLATE = dedent(
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
              REQUIRED_APPROVAL_LABEL: '__APPROVED_LABEL__'
              GOVERNED_FACT_FILES: '__GOVERNED_FACT_FILES__'
              GOVERNED_FACT_PREFIXES: '__GOVERNED_FACT_PREFIXES__'
              REVERT_MARKER: '__REVERT_MARKER__'
            run: |
              python - <<'PY'
              import json
              import os
              import subprocess
              import urllib.request

              ZERO = "0" * 40


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


              api_url = os.environ["GITHUB_API_URL"].rstrip("/")
              repo = os.environ["GITHUB_REPOSITORY"]
              token = os.environ["GH_TOKEN"]
              before = os.environ["PUSH_BEFORE"]
              after = os.environ["PUSH_AFTER"]
              required_label = os.environ["REQUIRED_APPROVAL_LABEL"]
              governed_files = set(json.loads(os.environ["GOVERNED_FACT_FILES"]))
              governed_prefixes = tuple(json.loads(os.environ["GOVERNED_FACT_PREFIXES"]))
              revert_marker = os.environ["REVERT_MARKER"]

              if not after or before == ZERO:
                  print("initial main bootstrap; main guard skipped")
                  raise SystemExit(0)

              head_message = run_git("log", "-1", "--pretty=%B", after)
              if head_message.startswith(revert_marker):
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

              request = urllib.request.Request(
                  f"{api_url}/repos/{repo}/commits/{after}/pulls",
                  headers={
                      "Accept": "application/vnd.github+json",
                      "Authorization": f"Bearer {token}",
                  },
              )
              try:
                  with urllib.request.urlopen(request, timeout=30) as response:
                      payload = json.loads(response.read().decode("utf-8") or "[]")
              except Exception as exc:  # noqa: BLE001
                  raise SystemExit(f"failed to resolve commit-associated PRs: {exc}") from exc
              if not isinstance(payload, list):
                  raise SystemExit("unexpected commit pull request payload from GitHub API")

              approved_pr = None
              for item in payload:
                  if not isinstance(item, dict):
                      raise SystemExit("malformed commit pull request payload from GitHub API")
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
                      approved_pr = item
                      break
              if approved_pr is not None:
                  print(f"governed push matched approved PR #{approved_pr.get('number')}")
                  raise SystemExit(0)

              commits = [
                  line.strip()
                  for line in run_git("rev-list", f"{before}..{after}").splitlines()
                  if line.strip()
              ]
              if not commits:
                  print("no commits to revert")
                  raise SystemExit(0)

              run_git("config", "user.name", "github-actions[bot]")
              run_git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
              for commit in commits:
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

              summary = ", ".join(governed_paths[:10])
              if len(governed_paths) > 10:
                  summary = f"{summary}, ... (+{len(governed_paths) - 10} more)"
              run_git(
                  "commit",
                  "-m",
                  f"{revert_marker} {after[:12]}",
                  "-m",
                  (
                      "Reason: governed fact state reached main without a merged approved PR.\n"
                      f"Paths: {summary}"
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
    "__APPROVED_LABEL__", LABEL_STATE_APPROVED
).replace(
    "__REVERT_MARKER__", MAIN_GUARD_REVERT_MARKER
)


def workflow_templates() -> dict[str, str]:
    return {
        L1_WORKFLOW_NAME: L1_WORKFLOW_TEMPLATE,
        L2_WORKFLOW_NAME: L2_WORKFLOW_TEMPLATE,
        APPROVAL_GATE_WORKFLOW_NAME: APPROVAL_GATE_WORKFLOW_TEMPLATE,
        MAIN_GUARD_WORKFLOW_NAME: MAIN_GUARD_WORKFLOW_TEMPLATE,
    }


def write_workflow_templates(repo_root: Path) -> list[Path]:
    workflows_dir = repo_root / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, template in workflow_templates().items():
        path = workflows_dir / filename
        path.write_text(template)
        written.append(path)
    return written
