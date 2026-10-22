"""The SessionStart hook: what a new session is told about the machine's shared state."""

import importlib.util
import json
import subprocess
import sys

from livery import simlock
from livery.config import REPO
from livery.review import queue
from tests.reviewpage import item

_spec = importlib.util.spec_from_file_location(
    "session_status", REPO / ".claude" / "hooks" / "session_status.py"
)
assert _spec is not None
assert _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

QUEUE_AND_EXIT = """
import json, sys
from pathlib import Path
from livery.review import queue
queue.enqueue(Path(sys.argv[1]), json.loads(sys.argv[2]))
"""


def test_a_review_whose_session_died_and_one_whose_apply_failed_are_reported(tmp_path):
    died, failed, other = tmp_path / "died", tmp_path / "failed", tmp_path / "other"
    for d in (died, failed, other):
        d.mkdir()
    items = [item("concept:x"), item("concept:y")]
    subprocess.run([sys.executable, "-c", QUEUE_AND_EXIT, str(died), json.dumps(items)], check=True)
    gid = queue.enqueue(failed, items)
    queue.finish(gid, {"error": "CalledProcessError: git commit failed"})
    queue.enqueue(other, items)  # its session waits: the only one the others are told of

    dead, waiting = hook._reviews(died)
    assert "died" in dead
    assert "1 review" in waiting
    broken, waiting = hook._reviews(failed)
    assert "failed" in broken
    assert "git commit failed" in broken
    assert "1 review" in waiting


def test_the_sim_lock_is_reported_only_while_it_is_held():
    with simlock.sim_lock("preview sim alpha"):
        [held] = hook._lock()
    assert "preview sim alpha" in held
    assert hook._lock() == []
