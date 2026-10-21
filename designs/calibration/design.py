"""Kit calibration on the McLaren: the library's calibration livery, which `livery kit review` shoots on any
kit, built as a fixture so a library change that moves it shows in `livery build --all`."""

from livery.calibration import calibration

KIT = "mclaren-720s-gt3-evo"
ROLE = "fixture"


def build(kit, p):
    return calibration(kit)
