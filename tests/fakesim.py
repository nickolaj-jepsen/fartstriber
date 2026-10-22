"""Putting test_simsession's fake sim in place of the real one, for tests of the commands that drive it."""

import irsdk

from livery import capture, simsession
from tests.test_simsession import FakeCapture, FakeIR, FakeWindow, World

QUICK = {
    "POLL_S": 0.01,
    "SETTLE_S": 0.0,
    "GREY_S": 0.2,
    "PAINT_S": 0.2,
    "LOAD_S": 0.0,
    "STEADY_S": 0.5,
    "UI_RETRY_S": 0.0,
    "CLOSING_POLL_S": 0.0,
    "READY_WAIT_S": 0.0,  # a sim that isn't ready is refused at once
}


def install(monkeypatch, world: World, ir: type[FakeIR] = FakeIR) -> World:
    world.folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(irsdk, "IRSDK", lambda: ir(world))
    monkeypatch.setattr(capture.Window, "find", staticmethod(lambda process: FakeWindow(world)))
    monkeypatch.setattr(capture, "Capture", FakeCapture)
    monkeypatch.setattr(capture, "processes", lambda: ["explorer.exe"])
    for name, value in QUICK.items():
        monkeypatch.setattr(simsession, name, value)
    return world


def drive(world: World, paint_folder: str) -> World:
    """The world's car is the one in this paint folder."""
    world.info["DriverInfo"]["Drivers"][0]["CarPath"] = paint_folder
    world.folder = world.cfg.paint_root / paint_folder
    world.folder.mkdir(parents=True, exist_ok=True)
    world.shown = world.on_disk()
    return world
