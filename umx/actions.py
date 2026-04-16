from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from umx.governance import GOVERNANCE_REVIEW_TRIGGER_LABELS


CHECKOUT_ACTION = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"  # v4.2.2
L1_WORKFLOW_NAME = "l1-dream.yml"
L2_WORKFLOW_NAME = "l2-review.yml"


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
          - name: Install umx
            run: python -m pip install umx
          - name: Run L1 dream
            run: umx dream --mode remote --tier l1
            env:
              UMX_PROVIDER: groq
              GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
    """
).replace("__CHECKOUT_ACTION__", CHECKOUT_ACTION)


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
          - name: Install umx
            run: python -m pip install umx
          - name: Run L2 review
            run: umx dream --mode remote --tier l2 --pr ${{ github.event.pull_request.number }} --head-sha ${{ github.event.pull_request.head.sha }}
            env:
              GH_TOKEN: ${{ github.token }}
              ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    """
).replace("__L2_REVIEW_CONDITION__", _l2_review_condition()).replace("__CHECKOUT_ACTION__", CHECKOUT_ACTION)


def workflow_templates() -> dict[str, str]:
    return {
        L1_WORKFLOW_NAME: L1_WORKFLOW_TEMPLATE,
        L2_WORKFLOW_NAME: L2_WORKFLOW_TEMPLATE,
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
