"""When agents may start the sim, and when an agent-started sim has to go."""

import time
from dataclasses import replace

import pytest

from livery import config, presence, simrule
from livery.presence import Presence
from livery.simrule import Launch

SIM = "iRacingSim64DX11.exe"
RULES = Launch(
    rule="idle",
    idle_minutes=5,
    close_after_minutes=30,
    display_mode="monitor",
    full_screen_stops=True,
    battery_stops=True,
    ui_closes=True,
)
AWAY = Presence(idle_s=3600, notification=presence.ACCEPTS, foreground="explorer.exe", battery=False)
HERE = Presence(idle_s=2, notification=presence.ACCEPTS, foreground="Code.exe", battery=False)
GAMING = Presence(idle_s=1, notification=presence.D3D_FULL_SCREEN, foreground="game.exe", battery=False)


def test_an_agent_starts_the_sim_once_the_owner_has_been_away():
    assert simrule.blocked(RULES, AWAY, SIM, None) is None
    assert simrule.blocked(RULES, HERE, SIM, None) is not None


def test_without_settings_or_with_never_agents_never_start_it():
    assert simrule.blocked(None, AWAY, SIM, None) is not None
    never = replace(RULES, rule="never")
    assert simrule.blocked(never, AWAY, SIM, None) is not None


def test_anytime_starts_it_while_the_owner_is_here():
    anytime = replace(RULES, rule="anytime")
    assert simrule.blocked(anytime, HERE, SIM, None) is None


def test_the_owners_allow_starts_it_while_they_are_here_and_their_hold_stops_it():
    allow = simrule.Override("allow", None)
    hold = simrule.Override("hold", None)
    assert simrule.blocked(RULES, HERE, SIM, allow) is None
    assert simrule.blocked(RULES, AWAY, SIM, hold) is not None


def test_a_full_screen_game_or_battery_stops_a_launch_even_when_allowed():
    allow = simrule.Override("allow", None)
    assert simrule.blocked(RULES, GAMING, SIM, allow) is not None
    on_battery = replace(AWAY, battery=True)
    assert simrule.blocked(RULES, on_battery, SIM, allow) is not None
    lenient = replace(RULES, full_screen_stops=False, battery_stops=False)
    assert simrule.blocked(lenient, GAMING, SIM, allow) is None


def test_the_sim_itself_in_front_isnt_a_full_screen_app():
    sim_in_front = Presence(idle_s=3600, notification=presence.BUSY, foreground=SIM, battery=False)
    assert simrule.blocked(RULES, sim_in_front, SIM, None) is None
    assert simrule.must_close(RULES, sim_in_front, SIM, None) is None


def test_a_full_screen_game_closes_an_agent_sim_at_once():
    found = simrule.must_close(RULES, GAMING, SIM, None)
    assert found is not None
    assert found[1] is True


@pytest.mark.parametrize(
    ("p", "o"),
    [
        (AWAY, simrule.Override("hold", None)),
        (Presence(idle_s=3, notification=presence.ACCEPTS, foreground="iRacingUI.exe", battery=False), None),
    ],
)
def test_a_hold_or_the_owner_in_the_iracing_ui_closes_it_after_the_run(p, o):
    found = simrule.must_close(RULES, p, SIM, o)
    assert found is not None
    assert found[1] is False


def test_the_iracing_ui_left_in_front_while_the_owner_is_away_closes_nothing():
    ui_idle = Presence(idle_s=3600, notification=presence.ACCEPTS, foreground="iRacingUI.exe", battery=False)
    assert simrule.must_close(RULES, ui_idle, SIM, None) is None


def test_an_unwanted_sim_closes_after_close_after_minutes():
    assert not simrule.idle_close(RULES, AWAY, quiet_s=29 * 60, in_front=False)
    assert simrule.idle_close(RULES, AWAY, quiet_s=30 * 60, in_front=False)


def test_an_unwanted_sim_in_front_waits_until_the_owner_is_away():
    assert not simrule.idle_close(RULES, HERE, quiet_s=3600, in_front=True)
    assert simrule.idle_close(RULES, AWAY, quiet_s=3600, in_front=True)


def test_an_override_lasts_until_it_expires_or_is_lifted():
    simrule.set_override("hold", 60)
    o = simrule.override()
    assert o is not None
    assert o.mode == "hold"
    assert simrule.override(now=time.time() + 120) is None
    simrule.set_override("allow")
    assert simrule.override(now=time.time() + 10**6) is not None
    simrule.set_override(None)
    assert simrule.override() is None


SETTINGS = {
    "launch": '"idle"',
    "idle_minutes": "5",
    "close_after_minutes": "30",
    "display_mode": '"monitor"',
    "full_screen_stops": "true",
    "battery_stops": "true",
    "ui_closes": "true",
}


def with_sim(**change: str | None) -> None:
    """The fixture settings plus a [sim] table; a value of None leaves its key out."""
    rows = [f"{k} = {v}" for k, v in {**SETTINGS, **change}.items() if v is not None]
    path = config.user_file()
    path.write_text(path.read_text(encoding="utf-8") + "\n[sim]\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_without_a_sim_table_agents_dont_start_the_sim():
    assert simrule.settings() is None


def test_the_sim_table_says_when_agents_start_the_sim():
    with_sim(idle_minutes="2.5")
    launch = simrule.settings()
    assert launch is not None
    assert (launch.rule, launch.idle_minutes, launch.full_screen_stops) == ("idle", 2.5, True)


def test_a_sim_table_needs_every_key():
    with_sim(ui_closes=None)
    with pytest.raises(config.ConfigError) as e:
        simrule.settings()
    assert "sim.ui_closes" in str(e.value)


@pytest.mark.parametrize(
    "change",
    [
        {"launch": '"sometimes"'},
        {"display_mode": '"vr"'},
        {"idle_minutes": "-1"},
        {"close_after_minutes": "0"},
        {"battery_stops": '"yes"'},
    ],
)
def test_a_sim_setting_outside_its_choices_is_an_error_that_names_it(change):
    with_sim(**change)
    with pytest.raises(config.ConfigError) as e:
        simrule.settings()
    assert f"sim.{next(iter(change))}" in str(e.value)


def test_the_sim_settings_leave_the_build_toolchain_alone():
    from livery import hashing

    assert "livery.simrule" not in hashing.build_modules()
