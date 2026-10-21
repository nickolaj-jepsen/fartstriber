"""PostToolUse on Edit|Write: ruff fixes and formats the .py file just written and reports what is left, so
no session runs ruff by hand. F401 is never fixed: an import added one edit before its first use would go.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RUFF = shutil.which("ruff", path=os.path.dirname(sys.executable)) or "ruff"
MAX_LINES = 20


def _project(path: Path) -> Path | None:
    """The nearest folder above the file whose pyproject.toml configures ruff."""
    for folder in path.parents:
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            return folder if "[tool.ruff" in pyproject.read_text(encoding="utf-8") else None
    return None


def main() -> None:
    event = json.load(sys.stdin)
    path = Path(event.get("tool_input", {}).get("file_path", ""))
    if path.suffix != ".py" or not path.is_file() or (root := _project(path)) is None:
        return
    run = {"cwd": root, "capture_output": True, "text": True, "timeout": 20}
    fmt = [RUFF, "format", "--force-exclude", "--quiet", str(path)]
    fix = [RUFF, "check", "--fix", "--unfixable", "F401", "--force-exclude", "--output-format", "concise"]
    subprocess.run(fmt, **run)
    formatted = path.read_bytes()
    left = subprocess.run([*fix, "--quiet", str(path)], **run).stdout.strip().splitlines()
    if path.read_bytes() != formatted:  # a fix can leave lines the formatter would wrap
        subprocess.run(fmt, **run)
    if left:
        more = f"\n(+{len(left) - MAX_LINES} more)" if len(left) > MAX_LINES else ""
        context = "ruff, after fixing and formatting:\n" + "\n".join(left[:MAX_LINES]) + more
        out = {"hookEventName": "PostToolUse", "additionalContext": context}
        print(json.dumps({"hookSpecificOutput": out}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a broken hook must never block work
        sys.exit(0)
