"""The arena: walls, obstacles, odour sources with a patchy plume, light, looming objects.

Coordinates in metres, x to the right, y up, (0, 0) at the lower-left corner. Angles in radians,
counter-clockwise from +x. Time in seconds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import Obstacle, Scenario


@dataclass
class Hit:
    dist: float        # metres along the ray
    incidence: float   # angle between the ray and the surface normal, radians (0 = head-on)


@dataclass
class LoomingObject:
    x: float
    y: float
    vx: float
    vy: float
    r: float
    t_end: float       # disappears at this time (s)


class World:
    def __init__(self, sc: Scenario, rng: np.random.Generator):
        self.sc = sc
        self.w = sc.arena.width_m
        self.h = sc.arena.height_m
        self.obstacles: list[Obstacle] = list(sc.obstacles)
        self.sources = [dict(x=s.x, y=s.y, strength=s.strength, spread=s.spread_m, label=s.label)
                        for s in sc.sources]
        self.day = sc.light.day
        self.ambient_override: float | None = None   # a phase's fixed light level (rest phases)
        self.looming: list[LoomingObject] = []
        # Patchy plume: a few random travelling waves; fixed by the scenario seed.
        p = sc.plume
        k = 2 * math.pi / max(p.patch_scale_m, 1e-3)
        n = 6
        dirs = rng.uniform(0, 2 * math.pi, n)
        self._kx = k * np.cos(dirs) * rng.uniform(0.7, 1.3, n)
        self._ky = k * np.sin(dirs) * rng.uniform(0.7, 1.3, n)
        self._om = 2 * math.pi / max(p.patch_period_s, 1e-3) * rng.uniform(0.6, 1.4, n)
        self._ph = rng.uniform(0, 2 * math.pi, n)

    # ------------------------------------------------------------------ odour and light

    def patch_factor(self, x: float, y: float, t: float) -> float:
        """Multiplicative plume patchiness, about 1 +/- patch_amp, never below 0.05."""
        amp = self.sc.plume.patch_amp
        if amp <= 0:
            return 1.0
        s = np.sin(self._kx * x + self._ky * y - self._om * t + self._ph).sum() / math.sqrt(len(self._ph))
        return max(0.05, 1.0 + amp * float(np.clip(s, -1.5, 1.5)) / 1.5)

    def odour(self, x: float, y: float, t: float) -> float:
        """True concentration at a point: each source falls off as 1 / (1 + (d / spread)^2)."""
        c = 0.0
        for s in self.sources:
            d2 = (x - s["x"]) ** 2 + (y - s["y"]) ** 2
            c += s["strength"] / (1.0 + d2 / s["spread"] ** 2)
        return self.sc.plume.background + c * self.patch_factor(x, y, t)

    def light_level(self, x: float | None = None, y: float | None = None) -> float:
        """Ambient (day/night) light, raised by any lamp (light patch) covering (x, y)."""
        if self.ambient_override is not None:
            return self.ambient_override
        level = self.sc.light.day_level if self.day else self.sc.light.night_level
        if x is None:
            return level
        base = level
        for p in self.sc.light.patches:
            d = math.hypot(x - p.x, y - p.y)
            if d <= p.r:
                level = max(level, p.level)
            elif d < p.r + p.edge_m:
                level = max(level, base + (p.level - base) * (1.0 - (d - p.r) / p.edge_m))
        return level

    # ------------------------------------------------------------------ dynamics

    def update(self, t: float, dt: float) -> None:
        for o in self.looming:
            o.x += o.vx * dt
            o.y += o.vy * dt
        self.looming = [o for o in self.looming
                        if t < o.t_end and -0.2 < o.x < self.w + 0.2 and -0.2 < o.y < self.h + 0.2]

    def launch_looming(self, rx: float, ry: float, heading: float, t: float) -> LoomingObject:
        """Throw an object straight at the robot from in front of it (keyboard 'L')."""
        lp = self.sc.looming
        d = lp.start_dist_m
        sx = min(max(rx + d * math.cos(heading), lp.radius_m), self.w - lp.radius_m)
        sy = min(max(ry + d * math.sin(heading), lp.radius_m), self.h - lp.radius_m)
        ang = math.atan2(ry - sy, rx - sx)
        dist = math.hypot(rx - sx, ry - sy)
        o = LoomingObject(sx, sy, lp.speed_mps * math.cos(ang), lp.speed_mps * math.sin(ang),
                          lp.radius_m, t + dist / lp.speed_mps)  # gone when it reaches the robot
        self.looming.append(o)
        return o

    # ------------------------------------------------------------------ geometry

    def collides(self, x: float, y: float, r: float) -> bool:
        """Does a circle of radius r at (x, y) touch a wall, obstacle or looming object?"""
        if x - r < 0 or y - r < 0 or x + r > self.w or y + r > self.h:
            return True
        for ob in self.obstacles:
            if ob.type == "circle":
                if math.hypot(x - ob.x, y - ob.y) < r + ob.r:
                    return True
            else:
                cx = min(max(x, ob.x), ob.x + ob.w)
                cy = min(max(y, ob.y), ob.y + ob.h)
                if math.hypot(x - cx, y - cy) < r:
                    return True
        return any(math.hypot(x - o.x, y - o.y) < r + o.r for o in self.looming)

    def cast(self, ox: float, oy: float, angle: float, max_range: float) -> Hit | None:
        """Nearest surface along a ray, or None within max_range."""
        dx, dy = math.cos(angle), math.sin(angle)
        best: Hit | None = None

        def consider(t: float, nx: float, ny: float) -> None:
            nonlocal best
            if 0.0 <= t <= max_range and (best is None or t < best.dist):
                best = Hit(t, math.acos(min(1.0, abs(dx * nx + dy * ny))))

        # Walls (the ray starts inside the arena).
        if dx > 1e-12:
            consider((self.w - ox) / dx, 1, 0)
        elif dx < -1e-12:
            consider(-ox / dx, 1, 0)
        if dy > 1e-12:
            consider((self.h - oy) / dy, 0, 1)
        elif dy < -1e-12:
            consider(-oy / dy, 0, 1)

        circles = [(ob.x, ob.y, ob.r) for ob in self.obstacles if ob.type == "circle"]
        circles += [(o.x, o.y, o.r) for o in self.looming]
        for cx, cy, r in circles:
            fx, fy = ox - cx, oy - cy
            b = fx * dx + fy * dy
            c = fx * fx + fy * fy - r * r
            disc = b * b - c
            if disc >= 0 and c > 0:  # c > 0: origin outside the circle
                t = -b - math.sqrt(disc)
                if t >= 0:
                    hx, hy = ox + t * dx - cx, oy + t * dy - cy
                    consider(t, hx / r, hy / r)

        for ob in self.obstacles:
            if ob.type != "rect":
                continue
            tmin, tmax, nx, ny = -math.inf, math.inf, 0.0, 0.0
            hit = True
            for o, d, lo, hi, axis in ((ox, dx, ob.x, ob.x + ob.w, 0), (oy, dy, ob.y, ob.y + ob.h, 1)):
                if abs(d) < 1e-12:
                    if not lo <= o <= hi:
                        hit = False
                    continue
                t1, t2 = (lo - o) / d, (hi - o) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                if t1 > tmin:
                    tmin = t1
                    nx, ny = (1.0, 0.0) if axis == 0 else (0.0, 1.0)
                tmax = min(tmax, t2)
            if hit and tmin <= tmax and tmin >= 0:
                consider(tmin, nx, ny)
        return best
