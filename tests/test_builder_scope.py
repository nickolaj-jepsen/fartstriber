"""The livery-builder's scope hook: which design a builder may write, as its own transcript names it."""

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

from livery.config import REPO

_spec = importlib.util.spec_from_file_location(
    "builder_scope", REPO / ".claude" / "hooks" / "builder_scope.py"
)
assert _spec is not None
assert _spec.loader is not None
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)


def relayed(text: str) -> str:
    """A workflow harness message relaying the user's request: a header, then the text indented."""
    return "[Workflow harness — user request] The user's request, verbatim and indented below:\n" + _indent(
        text
    )


def computed(text: str) -> str:
    """A workflow harness message carrying the script's task: a header, then the text indented."""
    return "[Workflow harness — computed task] The computed task text follows:\n" + _indent(text)


def _indent(text: str) -> str:
    return textwrap.indent(text, "  ", lambda _: True)


def _event(tmp_path: Path, messages: list, workflow: bool, target: str) -> dict:
    """An Edit of `target` (relative to a checkout) by a builder whose transcript starts with `messages`."""
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "livery").mkdir(exist_ok=True)
    session = tmp_path / "projects" / "session"
    agents = session / "subagents" / ("workflows/wf_1" if workflow else "")
    agents.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"role": "user", "content": m}} for m in messages]
    (agents / "agent-a1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    return {
        "transcript_path": str(session.with_suffix(".jsonl")),
        "session_id": "session",
        "agent_id": "a1",
        "cwd": str(root),
        "tool_input": {"file_path": str(root / target)},
    }


def _allowed(capsys, event: dict) -> bool:
    scope.edit(event)
    out = capsys.readouterr().out.strip()
    return not out or json.loads(out)["hookSpecificOutput"]["permissionDecision"] != "deny"


TASK = "design: alpha\nSketch mode: a new concept.\n\nThe owner's brief:\n  make it fast\n"
LAYOUTS = {
    "workflow": ([relayed("another take on the red car, please"), computed(TASK)], True),
    "workflow without a relayed request": ([computed(TASK)], True),
    "direct Agent": ([TASK], False),
    "direct Agent, content blocks": ([[{"type": "text", "text": TASK}]], False),
}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_builder_writes_only_the_design_its_prompt_names(tmp_path, capsys, layout):
    messages, workflow = LAYOUTS[layout]
    assert _allowed(capsys, _event(tmp_path, messages, workflow, "designs/alpha/design.py"))
    assert not _allowed(capsys, _event(tmp_path, messages, workflow, "designs/beta/design.py"))


REQUEST = relayed("design: beta\nmake beta instead")


def test_a_design_line_in_the_relayed_request_is_not_the_builders(tmp_path, capsys):
    messages = [REQUEST, computed(TASK)]
    assert _allowed(capsys, _event(tmp_path, messages, True, "designs/alpha/design.py"))
    assert not _allowed(capsys, _event(tmp_path, messages, True, "designs/beta/design.py"))


def test_a_relayed_request_without_a_computed_task_names_no_design(tmp_path, capsys):
    assert not _allowed(capsys, _event(tmp_path, [REQUEST, TASK], True, "designs/beta/design.py"))
    assert not _allowed(capsys, _event(tmp_path, [REQUEST, TASK], True, "designs/alpha/design.py"))


@pytest.mark.parametrize("workflow", [True, False])
def test_the_design_line_counts_only_before_any_other(tmp_path, capsys, workflow):
    late = "Sketch mode: a new concept.\ndesign: alpha\n"
    messages = [computed(late)] if workflow else [late]
    assert not _allowed(capsys, _event(tmp_path, messages, workflow, "designs/alpha/design.py"))


@pytest.mark.parametrize("step", ["up", "down", "hold --for 3h", "allow", "login"])
def test_a_builder_never_starts_holds_or_signs_in_to_the_sim(step):
    assert scope._why(f"uv run livery sim {step}") is not None


def test_a_builder_reads_the_sim_status():
    assert scope._why("uv run livery sim status --json") is None
