"""PreToolUse on Edit|Write: records only the owner makes.

An approval is the owner's accept of one build, so only `livery review` writes it. prefs/<person>/log.md is
that person's words: adding to it is the agent's job after a review, changing what is there needs their yes.
"""

import json
import sys
from pathlib import Path


def _decide(decision: str, reason: str) -> None:
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": out}))


def _only_adds(tool: str, args: dict, path: Path) -> bool:
    """True when the call keeps every existing character of the file and only inserts text."""
    if not path.exists():
        return True
    if tool == "Edit":
        old, new = args.get("old_string", ""), args.get("new_string", "")
        return bool(old) and (new.startswith(old) or new.endswith(old))
    before = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
    return args.get("content", "").replace("\r\n", "\n").startswith(before)


def main() -> None:
    event = json.load(sys.stdin)
    args = event.get("tool_input", {})
    path = Path(args.get("file_path", ""))
    parts = [p.lower() for p in path.parts]
    if parts[-1:] == ["approved.json"] and parts[-3:-2] == ["designs"]:
        _decide(
            "deny",
            "approved.json records the owner's accept of one build and only `livery review` writes it. "
            "Put the design up for review instead.",
        )
    elif (
        parts[-1:] == ["log.md"]
        and parts[-3:-2] == ["prefs"]
        and not _only_adds(event.get("tool_name", ""), args, path)
    ):
        _decide(
            "ask",
            "prefs/<person>/log.md is the owner's words. This edit changes lines already there instead of adding new ones.",
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a broken hook must never block work
        sys.exit(0)
