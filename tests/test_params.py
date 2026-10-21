from dataclasses import dataclass

import pytest

from livery.params import DesignParams, ParamError, param, parse_range, parse_sets, parse_wedge, with_sets


@dataclass(frozen=True)
class P(DesignParams):
    width: float = param(0.1, 0.0, 0.5)
    count: int = param(3, 1, 9)
    palette: str = param("dark", choices=("dark", "light"))
    gap: float = param(1, 0, 4)  # a float knob whose default is written as an int


def test_sets_are_typed_and_range_checked():
    p = with_sets(P(), parse_sets(["width=0.25", "count=5", "palette=light"]))
    assert (p.width, p.count, p.palette) == (0.25, 5, "light")
    for bad in ({"width": "0.9"}, {"count": "x"}, {"count": "2.5"}, {"palette": "pink"}, {"nope": "1"}):
        with pytest.raises(ParamError):
            with_sets(P(), bad)


def test_a_value_is_coerced_by_the_knobs_annotation_not_its_default():
    assert with_sets(P(), {"gap": "0.5"}).gap == 0.5


def test_ranges_expand_to_values():
    assert parse_range("0..4..2") == ["0", "2", "4"]
    assert parse_range("0.1..0.3..0.1") == ["0.1", "0.2", "0.3"]


@pytest.mark.parametrize(
    "spec", ["0..1..0.35", "0.1..0.3..0.1", "0..10..3", "0..1..0.1", "2..5", "0..0.7..0.7"]
)
def test_a_range_covers_its_span_and_never_passes_the_end(spec):
    lo, hi, *rest = (float(x) for x in spec.split(".."))
    step = rest[0] if rest else 1.0
    values = [float(v) for v in parse_range(spec)]
    assert values[0] == lo
    assert values[-1] <= hi + 1e-9
    assert values[-1] + step > hi + 1e-9


def test_a_wedge_names_its_param_and_values():
    assert parse_wedge("width=0..0.2..0.1") == ("width", ["0", "0.1", "0.2"])


@pytest.mark.parametrize(
    "spec", ["width", "=0..1", "width=0..x", "width=1..0", "width=0..1..0", "width=0..1..2..3"]
)
def test_a_malformed_wedge_is_a_param_error(spec):
    with pytest.raises(ParamError):
        parse_wedge(spec)


def test_named_streams_follow_the_seed_and_not_each_other():
    a, b = P(seed=1), P(seed=2)
    assert a.rng("stripes").random() == P(seed=1).rng("stripes").random()
    assert a.rng("stripes").random() != b.rng("stripes").random()
    assert a.rng("stripes").random() != a.rng("dots").random()
