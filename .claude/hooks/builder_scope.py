"""The livery-builder's scope, run by the hooks in its definition (.claude/agents/livery-builder.md).

A builder owns one design: its prompt has the line `design: <name>` before any other, read from its own
transcript. The prompt is the transcript's first message or, in a workflow, the script's computed task; the
user request a workflow's harness relays before it is the user's text and never names the design.
`edit` (PreToolUse on Edit|Write) denies a path outside designs/<name>/ and the scratchpad.
`shell` (PreToolUse on Bash|PowerShell) denies commands that write files other than through the livery
CLI, uv run and git, and every livery step but a builder's (BUILDER_STEPS). A script run with
`uv run python` can still write anywhere, so `stop` (the agent's Stop) sends the builder back once when
files outside designs/ changed after its first edit or command.
"""

import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]  # the checkout whose settings run this hook


def checkout(start: Path) -> Path:
    """The checkout (main or a git worktree) that holds `start`: the nearest folder with a .git entry and the
    livery package. A builder in a worktree changes that worktree's designs/, not the main checkout's."""
    for d in (start, *start.parents):
        if (d / ".git").exists() and (d / "livery").is_dir():
            return d
    return ROOT


SCRATCH = Path(tempfile.gettempdir()).resolve() / "claude"  # the session scratchpads live below this
DESIGN_LINE = re.compile(r"design:\s*([A-Za-z0-9][\w-]*)")
# Heads each of a workflow agent's first messages at column zero; the text it carries is indented below.
FRAME = re.compile(r"\[Workflow harness\W+([a-z ]+)\]")
CLI_WRITES = ("runs/metrics.jsonl",)  # outside designs/, written by livery commands a builder runs
RECORDS = {
    "approved.json": "approved.json records the owner's accept of one build; only `livery review` writes it",
    "verdicts.jsonl": "verdicts.jsonl is the critic's record; answer its fixes with `livery critic answer`",
}
USE_EDIT = (
    "Edit design.py with the Edit tool: a builder writes only with Edit and Write, inside designs/{design}/ "
    "and the scratchpad, and its shell commands run the livery CLI, uv run, git and read-only tools."
)

# Shell commands, by their first word (lowercase, without .exe).
SEPARATORS = {";", "&&", "||", "|", "|&", "&", "\n", "(", ")", "{", "}"}
KEYWORDS = {"if", "then", "else", "elif", "elseif", "do", "while", "until", "time", "!", "try", "finally"}
HEADERS = {"for", "foreach", "in", "case", "esac", "fi", "done", "function", "catch"}
WRITERS = {
    *("cp", "mv", "rm", "rmdir", "mkdir", "touch", "ln", "install", "dd", "truncate", "tee", "chmod"),
    *("chown", "rsync", "tar", "unzip", "zip", "gzip", "patch", "curl", "wget", "robocopy", "xcopy"),
    *("set-content", "add-content", "out-file", "new-item", "copy-item", "move-item", "remove-item"),
    *("rename-item", "clear-content", "set-item", "tee-object", "export-csv", "export-clixml"),
    *("expand-archive", "compress-archive", "invoke-webrequest", "iwr", "sc", "ac", "ni", "cpi", "copy"),
    *("mi", "move", "ri", "del", "erase", "rd", "rni", "ren", "md"),
}
PYTHONS = {"python", "python3", "py", "pythonw"}
INTERPRETERS = {
    *PYTHONS,
    *("node", "deno", "bun", "perl", "ruby", "php", "awk", "gawk", "bash", "sh", "zsh", "pwsh"),
    *("powershell", "cmd", "wsl", "xargs", "invoke-expression", "iex", "start-process", "saps", "start"),
    *(".", "source"),
}
UV_VALUE_FLAGS = {"--with", "--python", "-p", "--project", "--directory", "--env-file", "--group", "--extra"}
# The livery steps a builder runs (from its definition, the livery-design skill and the workflows' builder
# prompts), by command and, for a group, subcommand; every other step is refused, a new one included.
BUILDER_STEPS: dict[str, set[str] | None] = {
    **dict.fromkeys(("lint", "check", "build", "sheet", "status")),
    "preview": {"flat", "sim"},
    "bake": {"status"},
    "critic": {"answer", "status"},
    "sim": {"status"},
}
# Whose the refused steps are, for the refusal to say.
SESSION_STEPS = {
    *("review", "install", "export", "kit", "stats", "config", "references", "build --all"),
    *("preview cameras", "bake check", "critic pack", "sim pick-frame"),
}
CRITIC_STEPS = {"critic record", "critic compare", "critic adversary", "critic rank"}
RAW_WRITES = re.compile(
    r"\b(set-content|add-content|out-file|new-item|copy-item|move-item|remove-item|rename-item|clear-content"
    r"|tee-object|export-csv|expand-archive|invoke-webrequest|start-process|invoke-expression)\b"
    r"|\[(system\.)?io\.(file|directory)\]::|writeall(text|bytes|lines)|appendall(text|lines)|-outfile\b",
    re.I,
)
NULL = {"/dev/null", "$null", "nul", "nul:"}


def _deny(reason: str) -> None:
    out = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": out}))


# Who the builder is


def _state(agent: str) -> Path:
    try:
        from livery import state

        return state.root() / "builders" / f"{agent}.json"
    except Exception:  # without the venv's livery, the scratch root still holds it
        return SCRATCH / "fartstriber-builders" / f"{agent}.json"


def _load(agent: str) -> dict:
    try:
        return json.loads(_state(agent).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(agent: str, data: dict) -> None:
    path = _state(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def _transcript(event: dict, agent: str) -> Path | None:
    """The agent's own transcript: next to the session's, under subagents/ (workflow agents one level down)."""
    main = Path(event.get("transcript_path") or "")
    name = f"agent-{agent}.jsonl"
    if main.name == name:
        return main
    for base in (
        main.with_suffix("") / "subagents",
        main.parent / str(event.get("session_id")) / "subagents",
    ):
        if base.is_dir():
            for found in base.rglob(name):
                return found
    return None


def _prompt(path: Path) -> list[str]:
    """The builder's prompt, as lines: the computed task's text if the harness's messages come first, else the
    first message. Nothing else counts, above all not the relayed user request."""
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f):
            content = json.loads(line).get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            frame = FRAME.match(content)
            if frame is None:
                return content.splitlines() if n == 0 else []
            if frame[1] == "computed task":
                return [t for t in content.splitlines() if t[:1].isspace()]  # the text, not the header
    return []


def _prompt_design(event: dict, agent: str) -> str | None:
    path = _transcript(event, agent)
    if path is None:
        return None
    first = next((t.strip() for t in _prompt(path) if t.strip()), "")
    m = DESIGN_LINE.fullmatch(first)
    return m[1] if m else None


def _changes(root: Path) -> dict[str, str] | None:
    """Every changed or untracked file outside designs/ (and the CLI's own records) in the checkout, with a
    content hash."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        return None
    paths, entries = [], out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC":  # a rename's old path follows
                i += 1
        i += 1
    found = {}
    for p in paths:
        if p.startswith("designs/") or p in CLI_WRITES:
            continue
        f = root / p
        found[p] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "gone"
    return found


def _builder(event: dict) -> dict:
    """The builder's design and the changes that were there before it first edited or ran anything."""
    agent = str(event.get("agent_id") or "")
    known = _load(agent) if agent else {}
    if not known.get("design") and agent:
        known["design"] = _prompt_design(event, agent)
    if "root" not in known:
        known["root"] = str(checkout(Path(event.get("cwd") or ROOT).resolve()))
    if "before" not in known:
        known["before"] = _changes(Path(known["root"]))
    if agent:
        _save(agent, known)
    return known


# The hooks


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


def edit(event: dict) -> None:
    design = _builder(event).get("design")
    if not design:
        _deny(
            "This builder doesn't know its design: its prompt starts with the line `design: <name>`. Write "
            "nothing another way; stop and report it."
        )
        return
    raw = Path(event.get("tool_input", {}).get("file_path", ""))
    path = (raw if raw.is_absolute() else Path(event.get("cwd") or ROOT) / raw).resolve()
    own = (checkout(path) / "designs" / design).resolve()
    if _inside(path, own):
        if path.name in RECORDS:
            _deny(RECORDS[path.name] + ".")
        return
    if _inside(path, SCRATCH) and "scratchpad" in (p.lower() for p in path.parts):
        return
    _deny(
        f"{path} is outside designs/{design}/, the only folder this builder changes (and its scratchpad). "
        "A change anywhere else (the library, a kit, another design) is reported as blocked, with what and why."
    )


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|<>(){}\n")
    lex.whitespace, lex.whitespace_split, lex.escape, lex.commenters = " \t\r", True, "", ""
    return list(lex)


def _segments(tokens: list[str]) -> list[list[str]]:
    segments, seg = [], []
    for t in tokens:
        if t in SEPARATORS:
            segments.append(seg)
            seg = []
        else:
            seg.append(t)
    return [s for s in [*segments, seg] if s]


def _name(word: str) -> str:
    name = PureWindowsPath(word).name.lower()
    return name.removesuffix(".exe")


def _harmless_target(target: str) -> bool:
    if target.lower() in NULL:
        return True
    if "$" in target or not target:
        return False
    return _inside(Path(target).resolve(), SCRATCH)


def _python(args: list[str]) -> str | None:
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == "-":
            break
        elif a.startswith("--"):
            continue
        elif a.startswith("-"):
            if "c" in a[1:]:
                return "it runs Python code given inline"
            if a == "-m":
                return None
            skip = a in ("-X", "-W")
        else:
            return None  # a script file
    return "it runs Python code from its input"


def _livery(args: list[str]) -> str | None:
    words = [a for a in args if not a.startswith("-")]
    if not words or "--help" in args:
        return None
    first, second = [*words, ""][:2]
    subs = BUILDER_STEPS.get(first, set())
    if first == "build" and "--all" in args:
        step = "build --all"
    elif first in BUILDER_STEPS and (subs is None or second in subs):
        return None
    else:
        pair = f"{first} {second}".strip()
        step = pair if pair in SESSION_STEPS | CRITIC_STEPS or first in BUILDER_STEPS else first
    if step in CRITIC_STEPS:
        return f"`livery {step}` records the critic's answer, which the session does"
    if step in SESSION_STEPS or first in SESSION_STEPS:
        return f"`livery {step}` is the session's step, not a builder's"
    return f"`livery {step}` is not a builder step; a builder runs only the livery steps its procedure names"


def _uv(args: list[str]) -> str | None:
    if not args or args[0] != "run":
        return "a builder runs only `uv run`, never a uv command that changes the environment"
    rest, i = args[1:], 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in UV_VALUE_FLAGS else 1
    if i >= len(rest):
        return None
    tool = _name(rest[i])
    if tool in PYTHONS:
        return _python(rest[i + 1 :])
    if tool == "livery":
        return _livery(rest[i + 1 :])
    if tool in INTERPRETERS:
        return f"it runs `{rest[i]}`, which can write any file"
    return None


def _segment(words: list[str]) -> str | None:
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):  # VAR=value cmd
        words = words[1:]
    if len(words) > 1 and words[0].startswith("$") and words[1] == "=":  # $x = cmd
        words = words[2:]
    while words and words[0].lower() in KEYWORDS:
        words = words[1:]
    if not words or words[0].lower() in HEADERS or words[0].startswith("$"):
        return None
    cmd, args = _name(words[0]), words[1:]
    if cmd in WRITERS:
        return f"`{words[0]}` writes files"
    if cmd == "sed" and any(re.match(r"^(-[A-Za-z]*i|--in-place)", a) for a in args):
        return "`sed -i` writes files"
    if cmd == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint") for a in args):
        return "that `find` changes files"
    if cmd in PYTHONS:
        return "it runs Python outside `uv run`"
    if cmd in INTERPRETERS:
        return f"it runs `{words[0]}`, which can write any file"
    if cmd == "uv":
        return _uv(args)
    if cmd == "livery":
        return _livery(args)
    return None


def _why(command: str) -> str | None:
    """Why a command may write files outside the builder's tools, or None."""
    if m := RAW_WRITES.search(command):
        return f"it calls {m[0]}, which writes files"
    try:
        tokens = _tokens(command)
    except ValueError:
        return "its quoting can't be read"
    for i, t in enumerate(tokens):
        if ">" in t and set(t) <= set("<>&|"):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if t.endswith("&") and (target.isdigit() or target == "-"):
                continue
            if not _harmless_target(target):
                return f"it redirects output into {target or 'a file'}"
    for seg in _segments(tokens):
        if seg[0].lower() == "git":
            continue
        if why := _segment(seg):
            return why
    return None


def shell(event: dict) -> None:
    design = _builder(event).get("design") or "<name>"
    if why := _why(event.get("tool_input", {}).get("command", "")):
        _deny(f"Not run: {why}. " + USE_EDIT.format(design=design))


def stop(event: dict) -> None:
    agent = str(event.get("agent_id") or "")
    known = _load(agent) if agent else {}
    root = Path(known.get("root") or checkout(Path(event.get("cwd") or ROOT).resolve()))
    before, now = known.get("before"), _changes(root)
    if before is None or now is None:
        _state(agent).unlink(missing_ok=True)
        return
    moved = sorted(p for p in before.keys() | now.keys() if before.get(p) != now.get(p))
    if moved and not event.get("stop_hook_active"):
        design = known.get("design") or "<name>"
        reason = (
            f"Files outside designs/{design}/ changed while you worked: {', '.join(moved)}. A builder changes "
            "only its own folder: put each back as it was (`git checkout -- <path>`, or remove a file you "
            "added), then finish, reporting anything you needed there as blocked."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return
    _state(agent).unlink(missing_ok=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    event = json.load(sys.stdin)
    {"edit": edit, "shell": shell, "stop": stop}[mode](event)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Outside the builder's scope is denied when the hook can't tell; a stop is never held up.
        if len(sys.argv) > 1 and sys.argv[1] in ("edit", "shell"):
            _deny(f"The builder scope hook failed ({type(e).__name__}: {e}); report it.")
        sys.exit(0)
