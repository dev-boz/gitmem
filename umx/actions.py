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


def workflow_templates() -> dict[str, str]:
    return {
        L1_WORKFLOW_NAME: L1_WORKFLOW_TEMPLATE,
        L2_WORKFLOW_NAME: L2_WORKFLOW_TEMPLATE,
        APPROVAL_GATE_WORKFLOW_NAME: APPROVAL_GATE_WORKFLOW_TEMPLATE,
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
