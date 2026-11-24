"""End-to-end dashboard smoke test (Phase 9). Run from the repository root with the brain venv:

    brain\\.venv\\Scripts\\python dashboard\\scripts\\smoke_all.py

Needs the local broker running (infra\\start-broker.cmd local) and a built dashboard (npm run build
in dashboard/). Starts brain (with the logger, scratch database), the logger API, the simulator in
real time and the dashboard (next start); then
  - HTTP: the login flow, the protected pages and APIs, the logger proxy;
  - MQTT: runs scripts/smoke.ts (the dashboard's own contract module, over WebSockets) and checks,
    with an independent subscriber, that every control message it published is contract-valid and
    that the brain acknowledged every brain/control command.
Everything it starts is stopped at the end. Exit code 0 = all checks passed.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
from flybot_brain.config import MqttSettings, read_env  # noqa: E402
from flybot_contract import Contract, ContractError  # noqa: E402

WORK = ROOT / ".tmp" / "smoke"
API_PORT, WEB_PORT = 8765, 3100
WEB = f"http://127.0.0.1:{WEB_PORT}"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}", flush=True)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.3)
    return False


def run_browser(base_env: dict, captured: list, errors: list, want: set) -> None:
    """Real headless Chromium (Playwright, browsers on D:): login, live panels, every control clicked,
    smoothness, phone layout, history; then validate every message the browser tab published."""
    env = {**base_env, "WEB_URL": WEB, "SCREEN_DIR": str(WORK),
           "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", r"D:\DevCache\ms-playwright")}
    before = len(captured)
    out = subprocess.run(["node", "scripts/browser_smoke.ts"], cwd=ROOT / "dashboard", capture_output=True, text=True,
                         timeout=300, env=env)
    line = next((l for l in out.stdout.splitlines() if l.startswith("{")), "{}")
    rep = json.loads(line)
    check("browser test ran", out.returncode == 0 and "error" not in rep, rep.get("error", out.stderr[-300:]))
    for name, value in rep.get("checks", {}).items():
        check(f"browser: {name}", value is not False, "" if value is True or value is False else str(value))
    for e in rep.get("consoleErrors", [])[:5]:
        print("    browser console:", e[:300])
    time.sleep(1.0)
    mine = [(n, m) for n, m, _ in captured[before:] if m.get("src") == rep.get("src")]
    kinds = {(n, m.get("m") or m.get("c") or (("r" + str(m["r"])) if n == "reward" else None)
              or (("s" + str(m["s"])) if n == "brain/sleep" else None)) for n, m in mine}
    manual = sum(1 for n, _ in mine if n == "cmd/manual")
    check("browser: every control published and contract-valid", want <= kinds and not errors,
          f"{len(mine)} messages from {rep.get('src')}, {manual} cmd/manual" if want <= kinds
          else f"missing {sorted(map(str, want - kinds))}")
    if rep.get("screenshots"):
        print("screenshots:", ", ".join(str(WORK / s) for s in rep["screenshots"]))


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    db = WORK / "flybot.db"
    for p in WORK.glob("flybot.db*"):
        p.unlink()
    env_file = read_env()
    local = (ROOT / "dashboard" / ".env.local").read_text(encoding="utf-8")
    password = next(l.split("=", 1)[1].strip() for l in local.splitlines() if l.startswith("DASHBOARD_PASSWORD="))
    check("broker reachable (1883, 9001)", port_open(1883) and port_open(9001))
    if not results[-1][1]:
        return 1
    base_env = {**os.environ, "PYTHONPYCACHEPREFIX": os.environ.get("PYTHONPYCACHEPREFIX", r"D:\Tools\cache\pycache"),
                "FLYBOT_DB_PATH": str(db), "NEXT_TELEMETRY_DISABLED": "1"}
    procs: list[subprocess.Popen] = []

    def start(name, cmd, cwd, extra=None):
        log = (WORK / f"{name}.log").open("w", encoding="utf-8")
        p = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env={**base_env, **(extra or {})},
                             shell=isinstance(cmd, str))
        procs.append(p)
        return p

    # independent validator: user brain reads every topic (contract section 4)
    contract = Contract(env_file.get("FLYBOT_ROBOT_ID", "01"))
    captured: list[tuple[str, dict, bool]] = []
    errors: list[str] = []
    s = MqttSettings.from_env()
    v = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="flybot-01-smoke-validator", protocol=mqtt.MQTTv311)
    v.username_pw_set(s.user, s.password)

    def on_msg(cl, ud, m):
        name = contract.name_of(m.topic)
        if name is None or not m.payload:
            return
        try:
            msg = json.loads(m.payload)
        except ValueError:
            return
        if str(msg.get("src", "")).startswith("dash-"):
            try:
                contract.decode(name, m.payload)
                captured.append((name, msg, bool(m.retain)))
            except ContractError as e:
                errors.append(f"{name}: {e}")
    v.on_connect = lambda cl, *a: cl.subscribe(contract.prefix + "#", qos=1)
    v.on_message = on_msg
    v.connect(s.host, s.port)
    v.loop_start()

    try:
        bp = str(ROOT / "brain" / ".venv" / "Scripts" / "python.exe")
        sp = str(ROOT / "sim" / ".venv" / "Scripts" / "python.exe")
        start("brain", [bp, "-B", "-m", "flybot_brain", "--controller", "neural", "--no-persistence"], ROOT / "brain")
        start("api", [bp, "-B", "-m", "flybot_brain.logger", "--db", str(db), "api", "--port", str(API_PORT)], ROOT / "brain")
        start("sim", [sp, "-B", "-m", "flybot_sim", "batch", "--realtime", "--trials", "60", "--duration", "150",
                      "--scenario", "default", "--out", str(WORK / "sim.csv")], ROOT / "sim")
        start("web", f"npx next start -p {WEB_PORT}", ROOT / "dashboard", {"LOGGER_API_URL": f"http://127.0.0.1:{API_PORT}"})
        check("logger API up", wait_port(API_PORT, 20))
        check("dashboard up", wait_port(WEB_PORT, 40))
        time.sleep(12)                                    # robot boot + odour warm-up in the simulator

        # ---------------------------------------------------------------- HTTP: login and protected routes
        with httpx.Client(base_url=WEB, follow_redirects=False, timeout=10) as h:
            r = h.get("/")
            check("page needs login", r.status_code in (302, 303, 307, 308) and "/login" in r.headers.get("location", ""),
                  f"{r.status_code}")
            check("API needs login", h.get("/api/mqtt-config").status_code == 401)
            check("login page served", h.get("/login").status_code == 200)
            r = h.post("/api/login", data={"password": "wrong"})
            check("wrong password refused", "error=1" in r.headers.get("location", "") and "flybot_session" not in r.cookies)
            r = h.post("/api/login", data={"password": password})
            check("right password signs in", r.status_code == 303 and "flybot_session" in h.cookies)
            check("redirects stay on the browser's host (relative Location)",
                  r.headers.get("location") == "/", r.headers.get("location", ""))
            check("live page served", h.get("/").status_code == 200)
            check("history page served", h.get("/history").status_code == 200)
            cfg = h.get("/api/mqtt-config").json()
            check("broker settings after login", cfg.get("url", "").startswith("ws://") and cfg.get("username") == "dashboard"
                  and bool(cfg.get("password")), cfg.get("url", ""))
            hh = h.get("/api/history/health")
            check("logger API through the dashboard", hh.status_code == 200 and hh.json().get("ok") is True)
            sn = h.get("/api/history/sensors", params={"minutes": 5}).json()
            check("history has live sensor rows", sn.get("count", 0) > 0, f"{sn.get('count')} rows")
            check("unknown history path refused", h.get("/api/history/secrets").status_code == 404)

        # ---------------------------------------------------------------- MQTT: live data and every control
        out = subprocess.run(["node", "scripts/smoke.ts"], cwd=ROOT / "dashboard", capture_output=True, text=True,
                             timeout=180, env=base_env)
        line = next((l for l in out.stdout.splitlines() if l.startswith("{")), "{}")
        rep = json.loads(line)
        check("smoke.ts ran", out.returncode == 0 and "error" not in rep, rep.get("error", out.stderr[-300:]))
        seen = rep.get("seen", {})
        check("live data reached the dashboard", rep.get("live") is True,
              ", ".join(f"{k} {seen.get(k, 0)}" for k in ("sensors", "status", "brain/state", "brain/status", "cmd/motor")))
        time.sleep(1.0)
        mine = [(n, m, r_) for n, m, r_ in captured if m.get("src") == rep.get("src")]
        check("no invalid dashboard messages", not errors, "; ".join(errors[:3]))
        kinds = {(n, m.get("m") or m.get("c") or (("r" + str(m["r"])) if n == "reward" else None)
                  or (("s" + str(m["s"])) if n == "brain/sleep" else None)) for n, m, _ in mine}
        want = {("cmd/mode", "manual"), ("cmd/mode", "auto"), ("cmd/mode", "estop"), ("cmd/manual", None),
                ("reward", "r1"), ("reward", "r-1"), ("brain/sleep", "sTrue"), ("brain/sleep", "sFalse"),
                ("brain/control", "state"), ("brain/control", "lesion"), ("brain/control", "save"),
                ("brain/control", "load"), ("brain/control", "reset")}
        check("every control published and contract-valid", want <= kinds,
              f"{len(mine)} messages; missing {sorted(map(str, want - kinds))}" if not want <= kinds else f"{len(mine)} messages")
        # A live-forwarded message never carries the retain flag (MQTT 3.1.1), so check the broker's
        # retained cmd/mode with a fresh subscription: it must be our last one, mode auto.
        got: list[tuple[dict, bool]] = []
        rc = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="flybot-01-smoke-retained", protocol=mqtt.MQTTv311)
        rc.username_pw_set(s.user, s.password)
        rc.on_connect = lambda cl, *a: cl.subscribe(contract.topic("cmd/mode"), qos=1)
        rc.on_message = lambda cl, ud, m: got.append((json.loads(m.payload), bool(m.retain)))
        rc.connect(s.host, s.port)
        rc.loop_start()
        time.sleep(1.5)
        rc.loop_stop()
        rc.disconnect()
        check("cmd/mode is retained (last one: auto, from this tab)",
              bool(got) and got[0][1] and got[0][0].get("src") == rep.get("src") and got[0][0].get("m") == "auto",
              str(got[0][0]) if got else "nothing retained")
        acks = rep.get("acks", {})
        bad = {k: v_ for k, v_ in acks.items() if (v_ is not True and "refused" not in k) or ("refused" in k and v_ is not False)}
        check("brain acknowledged every brain/control command", not bad and len(acks) >= 20, f"{len(acks)} acks; problems {bad}")
        seqs = {}
        ok_seq = True
        for n, m, _ in mine:
            if m["seq"] != seqs.get(n, -1) + 1:
                ok_seq = False
            seqs[n] = m["seq"]
        check("per-topic seq counts from 0 without gaps", ok_seq and all(n in seqs for n, _ in want))

        if "--browser" in sys.argv:
            run_browser(base_env, captured, errors, want)
    finally:
        v.loop_stop()
        v.disconnect()
        for p in procs:
            if p.poll() is None:
                if isinstance(p.args, str):
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
