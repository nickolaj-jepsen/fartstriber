"""`livery stats`: the loop's numbers from the committed records (each design's verdicts.jsonl and
prefs/decisions.jsonl) and this machine's telemetry (livery.metrics).

- Per design: whole sim runs and failed sim attempts, critic rounds, sim runs per round, the last round.
- Critic agreement: the critic's rank of each concept the owner picked, and each final decision beside the
  critic's call on the last round before it (on the same design hash when the decision recorded one), with
  its rating.
- What the critic saw that the lint missed, by kind.
- Time per stage, the time from sim shots to the verdict on them, and waits for the sim lock.
"""

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from livery import config, critic, metrics
from livery.config import Workspace
from livery.designs import role

# A lint_missed note's kind is the first whose words it uses (each a regex from a word's start): what a
# lint would have to know to catch it.
KINDS = (
    (
        "hidden by sim parts",
        r"light panel|side blade|skirt blade|carbon|hardware|led\b|tail-light|crash-structure",
    ),
    ("seams and shut lines", r"seam|shut line|panel edge|a-pillar|window|wheel arch"),
    (
        "3D shape",
        r"crease|curv|crown|faces down|foreshorten|bent|bend|fold|wrap|distort|upside|in 3d|3d shots|"
        r"only reads|chase view|from above|top shot|shadowed",
    ),
    ("type and marks", r"text\b|letters|wordmark|legible|sponsor|number|fireproof|mark"),
    ("edges and fragments", r"ragged|stepped|notch|jagged|fragment|clipped|sliver|pieces|hook|stub"),
    ("strokes and thin lines", r"stroke|unstroked|keyline|pinline|thin|hairline|red line"),
    ("colour and contrast", r"contrast|colou?r|tone|can't be seen|cannot be seen"),
)
PICKS = ("accept", "edit", "mix")  # what a concept decision that picks it says
SHOTS_DIR = re.compile(r"(\d{8}-\d{6})$")


def kind(note: str) -> str:
    text = note.lower()
    return next((k for k, words in KINDS if re.search(rf"\b(?:{words})", text)), "other")


def _when(stamp: str | None) -> datetime | None:
    """A record's time: verdicts write local minutes, the ledger and telemetry ISO with an offset."""
    if not stamp:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M", "%Y%m%d-%H%M%S"):
        try:
            t = datetime.strptime(stamp, fmt)
        except ValueError:
            continue
        return t if t.tzinfo else t.astimezone()
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def _spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    values = sorted(values)
    p90 = values[min(len(values) - 1, round(0.9 * (len(values) - 1)))]
    return {"n": len(values), "median": round(statistics.median(values), 2), "p90": round(p90, 2)}


def _verdicts(ws: Workspace) -> dict[str, list[dict]]:
    return {
        p.parent.name: critic.records(ws, p.parent.name) for p in sorted(ws.designs.glob("*/verdicts.jsonl"))
    }


def _ledger(ws: Workspace) -> list[dict]:
    from livery.review.apply import ledger

    return ledger(ws)


def _role(ws: Workspace, name: str) -> str:
    path = ws.design_dir(name) / "design.py"
    return role(path) if path.exists() else "gone"


def designs(ws: Workspace, events: list[dict], verdicts: dict[str, list[dict]]) -> list[dict]:
    sims: Counter[str] = Counter(r["design"] for r in events if r.get("loop") == "sim")
    failed: Counter[str] = Counter(
        r["design"]
        for r in events
        if r.get("command") == "preview sim" and r.get("outcome") != "ok" and isinstance(r.get("design"), str)
    )
    rows = []
    for name in sorted(set(sims) | set(failed) | set(verdicts)):
        rounds = [r for r in verdicts.get(name, []) if r.get("kind", "round") == "round"]
        last = rounds[-1] if rounds else {}
        rows.append(
            {
                "design": name,
                "role": _role(ws, name),
                "sim_runs": sims[name],
                "sim_failed": failed[name],
                "rounds": len(rounds),
                "sim_runs_per_round": round(sims[name] / len(rounds), 1) if rounds else None,
                "last": {"overall": last.get("overall"), "call": last.get("verdict")} if last else None,
            }
        )
    return rows


def _rankings(events: list[dict], verdicts: dict[str, list[dict]]) -> list[dict]:
    """Every concept ranking: the records in the concepts' verdicts, and the telemetry lines from before."""
    found = {}
    for recs in verdicts.values():
        for r in recs:
            if r.get("kind") == "ranking":
                found[(r["at"], tuple(sorted(r["concepts"])))] = {
                    "at": _when(r["at"]),
                    "ranking": r["ranking"],
                }
    for r in events:
        ranking = (r.get("critic") or {}).get("ranking") if r.get("loop") == "critic-concepts" else None
        if ranking:
            found[(r["timestamp"], tuple(sorted(ranking)))] = {
                "at": _when(r["timestamp"]),
                "ranking": ranking,
            }
    return sorted(found.values(), key=lambda r: r["at"] or datetime.min.astimezone())


def concepts(decisions: list[dict], rankings: list[dict]) -> list[dict]:
    """Each concept the owner picked in a review of several, with the critic's rank of it among the same
    set in the last ranking before the review."""
    reviews: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        if d.get("id", "").startswith("concept:"):
            reviews[d["review"]].append(d)
    rows = []
    for review, ds in reviews.items():
        names = sorted(d["id"].partition(":")[2] for d in ds)
        start = min(_when(d["at"]) or datetime.max.astimezone() for d in ds)
        ranked = [r for r in rankings if sorted(r["ranking"]) == names and r["at"] and r["at"] <= start]
        ranking = ranked[-1]["ranking"] if ranked else None
        for d in ds:
            if d.get("decision") in PICKS:
                name = d["id"].partition(":")[2]
                rows.append(
                    {
                        "review": review,
                        "pick": name,
                        "decision": d["decision"],
                        "of": len(names),
                        "critic_rank": ranking.index(name) + 1 if ranking else None,
                        "rating": d.get("rating"),
                    }
                )
    return rows


def finals(decisions: list[dict], verdicts: dict[str, list[dict]]) -> list[dict]:
    """Each final decision beside the critic's call: the ledger's verdict on the same design hash, else the
    last round recorded before it."""
    rows = []
    for d in decisions:
        if not d.get("id", "").startswith("final:"):
            continue
        name = d["id"].partition(":")[2]
        verdict, matched = d.get("critic"), "same design hash"
        if verdict is None:
            at = _when(d["at"])
            before = [
                r
                for r in verdicts.get(name, [])
                if r.get("kind", "round") == "round" and at and (_when(r.get("at")) or at) <= at
            ]
            verdict, matched = (before[-1], "last round before it") if before else (None, None)
        rows.append(
            {
                "design": name,
                "at": d["at"],
                "decision": d.get("decision"),
                "rating": d.get("rating"),
                "critic": verdict.get("verdict") if verdict else None,
                "overall": verdict.get("overall") if verdict else None,
                "matched": matched,
            }
        )
    return rows


def lint_missed(verdicts: dict[str, list[dict]]) -> dict[str, int]:
    kinds: Counter[str] = Counter()
    for recs in verdicts.values():
        for r in recs:
            if r.get("kind", "round") == "round":
                kinds.update(kind(note) for note in r.get("lint_missed") or [])
    return dict(kinds.most_common())


def stages(events: list[dict], verdicts: dict[str, list[dict]]) -> dict:
    """Seconds per command and per sim-run stage; minutes from sim shots to the verdict on them."""
    commands: dict[str, list[float]] = defaultdict(list)
    sim: dict[str, list[float]] = defaultdict(list)
    for r in events:
        d = r.get("durations_s") or {}
        if r.get("event") == "command" and d.get("total") is not None:
            commands[r["command"]].append(d["total"])
        elif r.get("loop") == "flat" and d.get("total") is not None:
            commands["preview flat"].append(
                d["total"]
            )  # before the wrapper, preview flat logged its own line
        elif r.get("loop") == "sim":
            shots = [v for k, v in d.items() if k.startswith("shot_") and v is not None]
            for k in ("lock_wait", "connect", "install", "reload", "total"):
                if d.get(k) is not None:
                    sim[k].append(d[k])
            if shots:
                sim["shots"].append(sum(shots))
    to_verdict = []
    for recs in verdicts.values():
        for r in recs:
            m = SHOTS_DIR.search(r.get("shots") or "") if r.get("kind", "round") == "round" else None
            shot, judged = (_when(m[1]), _when(r.get("at"))) if m else (None, None)
            if shot and judged and judged >= shot:
                to_verdict.append((judged - shot).total_seconds() / 60)
    return {
        "commands": {k: _spread(v) for k, v in sorted(commands.items())},
        "sim": {k: _spread(v) for k, v in sim.items()},
        "shots_to_verdict_min": _spread(to_verdict),
    }


def lock_waits(events: list[dict]) -> dict:
    """Waits for the sim lock, from each whole sim run: a wait over a second means another run held it."""
    waits = [
        r["durations_s"]["lock_wait"]
        for r in events
        if r.get("loop") == "sim" and (r.get("durations_s") or {}).get("lock_wait") is not None
    ]
    queued = [w for w in waits if w > 1]
    return {
        "runs": len(waits),
        "queued": len(queued),
        "median_queued_s": round(statistics.median(queued), 1) if queued else None,
        "longest_s": round(max(queued), 1) if queued else None,
        "total_min": round(sum(queued) / 60, 1),
    }


def launches(events: list[dict]) -> dict:
    """The sims agents started, from each sim host's line: how long to connect and to open the replay, how
    long each stayed open, and why it ended (numbers in the reasons dropped, so like reasons count together)."""
    hosts = [r for r in events if r.get("loop") == "sim-host"]

    def spread(key: str, scale: float = 1.0) -> dict:
        values = [(r.get("durations_s") or {}).get(key) for r in hosts]
        return _spread([v / scale for v in values if v is not None])

    ended = Counter(re.sub(r"\d+", "N", str(r.get("ended"))) for r in hosts)
    return {
        "hosts": len(hosts),
        "connect_s": spread("connect"),
        "ready_s": spread("ready"),
        "open_min": spread("total", 60.0),
        "ended": dict(ended.most_common()),
    }


def stats(ws: Workspace, telemetry: Path | None = None) -> dict:
    events = metrics.read(telemetry)
    verdicts = _verdicts(ws)
    decisions = _ledger(ws)
    return {
        "designs": designs(ws, events, verdicts),
        "concepts": concepts(decisions, _rankings(events, verdicts)),
        "finals": finals(decisions, verdicts),
        "lint_missed": lint_missed(verdicts),
        "stages": stages(events, verdicts),
        "lock": lock_waits(events),
        "launches": launches(events),
    }


def _fmt(s: dict) -> str:
    return f"{s['n']:4}  median {s['median']:7}  p90 {s['p90']:7}" if s["n"] else "   0"


def lines(report: dict) -> list[str]:
    owner = config.owner()
    out = ["designs                    role     sim runs  failed  rounds  runs/round  last"]
    for r in report["designs"]:
        last = f"{r['last']['call']} {r['last']['overall']}/10" if r["last"] else "-"
        per = "-" if r["sim_runs_per_round"] is None else r["sim_runs_per_round"]
        out.append(
            f"  {r['design']:24} {r['role']:8} {r['sim_runs']:8}  {r['sim_failed']:6}  {r['rounds']:6}  {per!s:10}  {last}"
        )
    out += ["", f"concepts {owner} picked: the critic's rank of the pick"]
    for r in report["concepts"]:
        rank = f"{r['critic_rank']} of {r['of']}" if r["critic_rank"] else "not ranked"
        rating = f", rated {r['rating']}" if r["rating"] else ""
        out.append(f"  {r['review']}  {r['pick']:24} {r['decision']:6} critic: {rank}{rating}")
    out += ["", f"finals: {owner}'s decision beside the critic's call"]
    table = Counter((r["decision"], r["critic"] or "none") for r in report["finals"])
    for (decision, call), n in sorted(table.items()):
        ratings = [
            r["rating"]
            for r in report["finals"]
            if (r["decision"], r["critic"] or "none") == (decision, call)
        ]
        rated = [x for x in ratings if x]
        out.append(f"  {decision:7} critic {call:8} {n:3}" + (f"  ratings {rated}" if rated else ""))
    out += ["", "what the lint missed, by kind"]
    out += [f"  {k:24} {n:3}" for k, n in report["lint_missed"].items()]
    st = report["stages"]
    out += ["", "seconds per command"]
    out += [f"  {k:24} {_fmt(v)}" for k, v in st["commands"].items()]
    out += ["", "seconds per sim-run stage"]
    out += [f"  {k:24} {_fmt(v)}" for k, v in st["sim"].items()]
    out += ["", f"  minutes from sim shots to their verdict {_fmt(st['shots_to_verdict_min'])}"]
    lock = report["lock"]
    out += [
        "",
        f"sim lock: {lock['queued']} of {lock['runs']} sim runs queued"
        + (
            f", median {lock['median_queued_s']} s, longest {lock['longest_s']} s, {lock['total_min']} min in all"
            if lock["queued"]
            else ""
        ),
    ]
    sims = report["launches"]
    if sims["hosts"]:
        out += ["", f"sims agents started: {sims['hosts']}"]
        out += [
            f"  {'seconds to connect':24} {_fmt(sims['connect_s'])}",
            f"  {'seconds to the replay':24} {_fmt(sims['ready_s'])}",
            f"  {'minutes open':24} {_fmt(sims['open_min'])}",
        ]
        out += [f"  ended: {why} ({n})" for why, n in sims["ended"].items()]
    return out
