"""Live view smoke test: draws frames and handles keys without a window (SDL dummy driver)."""
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame  # noqa: E402

from flybot_sim.config import load_scenario  # noqa: E402
from flybot_sim.contract import Contract  # noqa: E402
from flybot_sim.link import FakeLink  # noqa: E402
from flybot_sim.sim import Simulation  # noqa: E402
from flybot_sim.view import LiveView  # noqa: E402


def press(key):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))


def test_view_draws_and_keys_act():
    pygame.init()
    try:
        link = FakeLink(Contract())
        sim = Simulation(load_scenario("two_sources"), link)
        view = LiveView(sim)
        for _ in range(1600):            # 8 s of robot time: past boot
            sim.step()
        assert view.frame()

        x0 = sim.world.sources[0]["x"]
        for key in (pygame.K_RIGHT, pygame.K_l, pygame.K_n, pygame.K_g, pygame.K_b, pygame.K_TAB,
                    pygame.K_UP, pygame.K_h, pygame.K_SPACE):
            press(key)
        assert view.frame()
        assert sim.world.sources[0]["x"] > x0          # moved right
        assert sim.world.looming                       # looming object launched
        assert not sim.world.day                       # night
        assert [m["r"] for n, m in link.published if n == "reward"] == [1, -1]
        assert view.selected == 1 and view.paused and not view.show_heat

        press(pygame.K_q)
        assert view.frame() is False
    finally:
        pygame.quit()
