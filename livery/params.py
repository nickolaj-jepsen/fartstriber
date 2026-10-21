"""Typed design knobs and seeded randomness.

A design declares `@dataclass class Params(DesignParams)` whose fields are its knobs, each made with
`param(default, lo, hi)`. `build(kit, p)` reads only `p` for anything tunable, so `--set k=v` and wedge
sheets can try values without editing the script. Randomness comes only from `p.rng("name")`: a numpy
generator seeded from the design's `seed` and the stream's name, so adding one stream never shifts another.
"""

import dataclasses
import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from livery.errors import UsageError


def param(default: Any, lo: float | None = None, hi: float | None = None, choices: tuple = ()) -> Any:
    """A knob with its default and the range (or choices) a wedge or `--set` may use."""
    return field(default=default, metadata={"lo": lo, "hi": hi, "choices": tuple(choices)})


@dataclass(frozen=True)
class DesignParams:
    seed: int = param(0, 0, 2**31 - 1)

    def rng(self, key: str) -> np.random.Generator:
        digest = hashlib.sha256(f"{self.seed}:{key}".encode()).digest()
        return np.random.default_rng(int.from_bytes(digest[:8], "little"))


class ParamError(UsageError, ValueError):
    pass


SCALARS = {t.__name__: t for t in (bool, int, float, str)}


def _kind(f: dataclasses.Field) -> Any:
    """The knob's annotation (a string under postponed annotations), else its default's type."""
    kind = SCALARS.get(f.type) if isinstance(f.type, str) else f.type
    return kind if kind in SCALARS.values() else type(f.default)


def _coerce(f: dataclasses.Field, raw: str) -> Any:
    kind = _kind(f)
    try:
        if kind is bool:
            if raw.lower() not in ("true", "false", "1", "0"):
                raise ValueError
            value: Any = raw.lower() in ("true", "1")
        else:
            value = kind(raw)
    except ValueError:
        raise ParamError(f"{f.name}: {raw!r} is not a {kind.__name__}") from None
    meta = f.metadata
    if meta.get("choices") and value not in meta["choices"]:
        raise ParamError(f"{f.name}: {value!r} is not one of {meta['choices']}")
    if (meta.get("lo") is not None and value < meta["lo"]) or (
        meta.get("hi") is not None and value > meta["hi"]
    ):
        raise ParamError(f"{f.name}: {value} is outside {meta['lo']}..{meta['hi']}")
    return value


def with_sets(p: DesignParams, sets: dict[str, str]) -> DesignParams:
    """`p` with `name=value` overrides applied, each checked against its field's type and range."""
    fields = {f.name: f for f in dataclasses.fields(p)}
    unknown = sorted(set(sets) - set(fields))
    if unknown:
        raise ParamError(f"unknown params {unknown}; this design has {sorted(fields)}")
    return dataclasses.replace(p, **{k: _coerce(fields[k], v) for k, v in sets.items()})


def parse_sets(items: list[str] | None) -> dict[str, str]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ParamError(f"expected k=v, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_range(spec: str) -> list[str]:
    """`a..b..step` (or `a..b`, step 1) as the values to try, formatted for `with_sets`."""
    try:
        nums = [float(x) for x in spec.split("..")]
    except ValueError:
        nums = []
    if len(nums) not in (2, 3):
        raise ParamError(f"expected a..b or a..b..step, got {spec!r}")
    lo, hi, step = nums[0], nums[1], nums[2] if len(nums) == 3 else 1.0
    if not all(math.isfinite(x) for x in nums) or step <= 0 or hi < lo:
        raise ParamError(f"bad range {spec!r}")
    count = math.floor((hi - lo) / step + 1e-9) + 1  # the last step that doesn't pass b
    is_int = all(float(x).is_integer() for x in nums)
    return [str(int(lo + i * step)) if is_int else f"{lo + i * step:g}" for i in range(count)]


def parse_wedge(spec: str) -> tuple[str, list[str]]:
    """`k=a..b..step` as the knob's name and the values to try."""
    key, eq, rng = spec.partition("=")
    if not eq or not key:
        raise ParamError(f"expected k=a..b..step, got {spec!r}")
    return key, parse_range(rng)
