"""Live top-down view (pygame), real time.

Keys:
  arrows   move the selected odour source (5 cm)     TAB   select the next source
  L        launch a looming object at the robot      N     toggle day / night
  G        reward button (+1)                        B     punish button (-1)
  SPACE/P  pause / resume                            R     put the robot back at its start
  H        heat map on / off                         Q/ESC quit
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import numpy as np  # noqa: E402
import pygame  # noqa: E402

from .config import load_scenario  # noqa: E402
from flybot_contract import Contract  # noqa: E402
from .edge import BOOT, CLOUD, ESCAPE, ESTOP, FALLBACK, MANUAL, SLEEP  # noqa: E402
from .robot import Pose  # noqa: E402
from .sim import Simulation  # noqa: E402

ARENA_PX = 720
PANEL_PX = 320
BG = (24, 26, 30)
FLOOR = (40, 44, 50)
WALL = (200, 200, 205)
OBST = (120, 125, 135)
TEXT = (225, 228, 232)
DIM = (140, 145, 155)
STATE_COLOURS = {BOOT: (150, 150, 150), CLOUD: (80, 200, 120), MANUAL: (90, 150, 240),
                 ESCAPE: (240, 80, 80), FALLBACK: (245, 160, 60), SLEEP: (170, 120, 230),
                 ESTOP: (200, 40, 40)}


class LiveView:
    def __init__(self, sim: Simulation, screen: pygame.Surface | None = None):
        self.sim = sim
        self.scale = ARENA_PX / max(sim.world.w, sim.world.h)
        self.screen = screen or pygame.display.set_mode((ARENA_PX + PANEL_PX, ARENA_PX))
        pygame.display.set_caption(f"FlyBot simulator: {sim.sc.name}")
        self.font = pygame.font.SysFont("consolas", 15)
        self.big = pygame.font.SysFont("consolas", 22, bold=True)
        self.clock = pygame.time.Clock()
        self.paused = False
        self.running = True
        self.selected = 0
        self.show_heat = True
        self.heat: pygame.Surface | None = None
        self.heat_t = -1e9
        self.message = ""

    # ------------------------------------------------------------------ coordinates

    def px(self, x: float, y: float) -> tuple[int, int]:
        return int(x * self.scale), int(ARENA_PX - y * self.scale)

    # ------------------------------------------------------------------ input

    def handle_events(self) -> None:
        sim = self.sim
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                k = ev.key
                step = 0.05
                if k in (pygame.K_q, pygame.K_ESCAPE):
                    self.running = False
                elif k in (pygame.K_SPACE, pygame.K_p):
                    self.paused = not self.paused
                elif k == pygame.K_TAB:
                    self.selected = (self.selected + 1) % len(sim.world.sources)
                elif k == pygame.K_LEFT:
                    sim.move_source(self.selected, -step, 0)
                elif k == pygame.K_RIGHT:
                    sim.move_source(self.selected, step, 0)
                elif k == pygame.K_UP:
                    sim.move_source(self.selected, 0, step)
                elif k == pygame.K_DOWN:
                    sim.move_source(self.selected, 0, -step)
                elif k == pygame.K_l:
                    sim.launch_looming()
                    self.message = "looming object launched"
                elif k == pygame.K_n:
                    sim.toggle_day()
                    self.message = "day" if sim.world.day else "night"
                elif k == pygame.K_g:
                    sim.press_reward(1)
                    self.message = "reward +1 sent"
                elif k == pygame.K_b:
                    sim.press_reward(-1)
                    self.message = "punish -1 sent"
                elif k == pygame.K_h:
                    self.show_heat = not self.show_heat
                elif k == pygame.K_r:
                    sp = sim.start
                    sim.robot.pose = Pose(sp.x, sp.y, math.radians(sp.heading_deg))
                    sim.robot.vl = sim.robot.vr = 0.0
                    sim.trail.clear()
                    self.message = "robot back at start"
                if k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                    self.heat_t = -1e9      # redraw the heat map now

    # ------------------------------------------------------------------ drawing

    def _heat_map(self) -> pygame.Surface:
        t = self.sim.t_ms / 1000.0
        if self.heat is None or t - self.heat_t > 0.5:
            n = 48
            w = self.sim.world
            xs = (np.arange(n) + 0.5) / n * w.w
            ys = (np.arange(n) + 0.5) / n * w.h
            c = np.array([[w.odour(x, y, t) for x in xs] for y in ys[::-1]])
            v = np.clip(c / max(0.6, float(c.max())), 0, 1)
            rgb = np.zeros((n, n, 3), dtype=np.uint8)
            rgb[..., 0] = (40 + 200 * v).astype(np.uint8)
            rgb[..., 1] = (44 + 120 * v).astype(np.uint8)
            rgb[..., 2] = (50 + 20 * v).astype(np.uint8)
            surf = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
            self.heat = pygame.transform.smoothscale(surf, (ARENA_PX, ARENA_PX))
            self.heat_t = t
        return self.heat

    def draw(self) -> None:
        sim, scr = self.sim, self.screen
        scr.fill(BG)
        arena = scr.subsurface((0, 0, ARENA_PX, ARENA_PX))
        if self.show_heat:
            arena.blit(self._heat_map(), (0, 0))
        else:
            arena.fill(FLOOR)
        w = sim.world

        for ob in w.obstacles:
            if ob.type == "circle":
                pygame.draw.circle(arena, OBST, self.px(ob.x, ob.y), int(ob.r * self.scale))
            else:
                x0, y1 = self.px(ob.x, ob.y + ob.h)
                pygame.draw.rect(arena, OBST, (x0, y1, int(ob.w * self.scale), int(ob.h * self.scale)))

        for i, s in enumerate(w.sources):
            c = self.px(s["x"], s["y"])
            if i == 0:
                pygame.draw.circle(arena, (250, 220, 120), c, int(sim.sc.success_radius_m * self.scale), 1)
            pygame.draw.circle(arena, (255, 200, 60), c, 7)
            if i == self.selected:
                pygame.draw.circle(arena, (255, 255, 255), c, 11, 2)

        if len(sim.trail) > 1:
            pygame.draw.lines(arena, (110, 170, 255), False, [self.px(x, y) for x, y in sim.trail], 1)

        for o in w.looming:
            pygame.draw.circle(arena, (240, 70, 70), self.px(o.x, o.y), int(o.r * self.scale))

        self._draw_robot(arena)
        pygame.draw.rect(arena, WALL, (0, 0, ARENA_PX, ARENA_PX), 3)
        self._draw_panel()
        pygame.display.flip()

    def _draw_robot(self, arena: pygame.Surface) -> None:
        sim = self.sim
        r, p = sim.robot, sim.robot.pose
        # Ultrasonic cone and the measured echo.
        us = sim.ultrasonic
        ox, oy = us.origin(r)
        half = math.radians(us.p.cone_deg) / 2
        reach = (sim.raw_d_cm / 100.0) if sim.raw_d_cm is not None else 0.6
        pts = [self.px(ox, oy)] + [self.px(ox + reach * math.cos(p.theta + a), oy + reach * math.sin(p.theta + a))
                                   for a in np.linspace(-half, half, 9)]
        cone = pygame.Surface(arena.get_size(), pygame.SRCALPHA)
        colour = (120, 220, 255, 60) if sim.raw_d_cm is not None else (160, 160, 160, 35)
        pygame.draw.polygon(cone, colour, pts)
        arena.blit(cone, (0, 0))
        # Body and heading.
        col = STATE_COLOURS.get(sim.edge.state, TEXT)
        pygame.draw.circle(arena, col, self.px(p.x, p.y), int(r.p.radius_m * self.scale))
        pygame.draw.circle(arena, (20, 20, 20), self.px(p.x, p.y), int(r.p.radius_m * self.scale), 2)
        hx, hy = r.body_to_world(r.p.radius_m, 0.0)
        pygame.draw.line(arena, (20, 20, 20), self.px(p.x, p.y), self.px(hx, hy), 3)
        # Odour inlets, coloured by their 0-1 reading.
        for s, key in zip(sim.odour, ("ol", "or")):
            v = sim.values[key]
            ix, iy = s.inlet(r)
            c = (90, 90, 90) if v is None else (int(60 + 195 * v), int(200 - 120 * v), 60)
            pygame.draw.circle(arena, c, self.px(ix, iy), 5)

    def _draw_panel(self) -> None:
        sim, e, v = self.sim, self.sim.edge, self.sim.values
        x0, y = ARENA_PX + 16, 14
        scr = self.screen

        def line(text, colour=TEXT, font=None):
            nonlocal y
            scr.blit((font or self.font).render(text, True, colour), (x0, y))
            y += 22 if font is self.big else 19

        def bar(label, val, colour):
            nonlocal y
            scr.blit(self.font.render(f"{label:<6}", True, DIM), (x0, y))
            pygame.draw.rect(scr, (60, 64, 72), (x0 + 64, y + 3, 180, 11))
            if val is not None:
                pygame.draw.rect(scr, colour, (x0 + 64, y + 3, int(180 * min(max(val, 0), 1)), 11))
            scr.blit(self.font.render("--" if val is None else f"{val:.3f}", True, TEXT), (x0 + 252, y))
            y += 19

        line(e.state.upper(), STATE_COLOURS.get(e.state, TEXT), self.big)
        line(f"mode {e.mode}   {'MQTT ok' if sim.link.connected else 'MQTT DOWN'}",
             TEXT if sim.link.connected else (240, 90, 90))
        line(f"robot time {sim.t_ms / 1000:7.1f} s" + ("   PAUSED" if self.paused else ""))
        line(f"trial time {sim.trial_time_s():7.1f} s")
        y += 6
        bar("odourL", v["ol"], (240, 170, 60))
        bar("odourR", v["or"], (240, 170, 60))
        bar("light", v["lt"], (240, 240, 140))
        dist = "--" if v["d"] is None else f"{v['d']:.1f} cm"
        line(f"dist   {dist}")
        line(f"bat    {v['bat']:.2f} V")
        line(f"motor  L {e.out[0]:+.2f}  R {e.out[1]:+.2f}")
        y += 6
        res = sim.results()
        line(f"reached     {'YES ' + str(res['time_to_source_s']) + ' s' if res['reached'] else 'no'}")
        line(f"collisions  {res['collisions']}")
        line(f"escapes     {res['escapes']}")
        line(f"fallback    {res['fallback_time_s']:.1f} s")
        line(f"cmd/motor   {res['motor_cmds']}   lost {res['messages_lost']}")
        rtt = sim._median(sim.m.rtts, sim.t_ms)
        crtt = sim._median(sim.m.crtts, sim.t_ms)
        line(f"rtt {'--' if rtt is None else rtt} ms   crtt {'--' if crtt is None else crtt} ms")
        line(f"{'day' if sim.world.day else 'night'}   source {self.selected + 1}/{len(sim.world.sources)}")
        y += 8
        for k in ("arrows move source", "TAB next source", "L looming   N day/night",
                  "G reward    B punish", "SPACE pause R reset", "H heat map  Q quit"):
            line(k, DIM)
        if self.message:
            y += 6
            line(self.message, (250, 220, 120))

    # ------------------------------------------------------------------ loop hook

    def frame(self) -> bool:
        self.handle_events()
        self.draw()
        self.clock.tick(60)
        return self.running


def run_live(args) -> int:
    from .__main__ import open_link
    from .runner import run_realtime

    pygame.init()
    sc = load_scenario(args.scenario)
    contract = Contract()
    link = open_link(contract)
    sim = Simulation(sc, link, contract, start_index=args.start, seed=args.seed)
    view = LiveView(sim)
    try:
        run_realtime(sim, on_frame=view.frame, paused=lambda: view.paused)
    finally:
        link.stop()
        pygame.quit()
    return 0
