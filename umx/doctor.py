from __future__ import annotations

from pathlib import Path

from umx.conventions import validate_conventions_file
from umx.scope import get_umx_home, project_memory_dir


def run_doctor(cwd: Path | None = None) -> dict[str, object]:
    home = get_umx_home()
    result: dict[str, object] = {
        "umx_home": str(home),
        "exists": home.exists(),
        "config_exists": (home / "config.yaml").exists(),
    }

    # Convention validation
    try:
        repo_dir = project_memory_dir(cwd)
        conventions_path = repo_dir / "CONVENTIONS.md"
        conv_issues = validate_conventions_file(conventions_path)
        result["conventions_valid"] = len(conv_issues) == 0
        result["conventions_issues"] = conv_issues
    except Exception:
        result["conventions_valid"] = False
        result["conventions_issues"] = ["could not locate project memory"]

    return result
