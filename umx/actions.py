from __future__ import annotations

from pathlib import Path
from textwrap import dedent


L1_WORKFLOW_NAME = "l1-dream.yml"
L2_WORKFLOW_NAME = "l2-review.yml"


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
      pull-requests: write

    concurrency:
      group: dream-l1-${{ github.ref }}
      cancel-in-progress: true

    jobs:
      dream:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - name: Install umx
            run: python -m pip install .
          - name: Run L1 dream
            run: umx dream --mode remote --tier l1
            env:
              UMX_PROVIDER: groq
              GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
    """
)


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
      contents: read
      pull-requests: write

    concurrency:
      group: dream-l2-${{ github.event.pull_request.number }}
      cancel-in-progress: true

    jobs:
      review:
        runs-on: ubuntu-latest
        if: >-
          contains(github.event.pull_request.labels.*.name, 'type: extraction') ||
          contains(github.event.pull_request.labels.*.name, 'type: consolidation') ||
          contains(github.event.pull_request.labels.*.name, 'type: deletion') ||
          contains(github.event.pull_request.labels.*.name, 'type: gap-fill') ||
          contains(github.event.pull_request.labels.*.name, 'type: lint') ||
          contains(github.event.pull_request.labels.*.name, 'type: principle') ||
          contains(github.event.pull_request.labels.*.name, 'type: supersession')
        steps:
          - uses: actions/checkout@v4
          - name: Install umx
            run: python -m pip install .
          - name: Run L2 review
            run: umx dream --mode remote --tier l2 --pr ${{ github.event.pull_request.number }}
            env:
              ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    """
)


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
