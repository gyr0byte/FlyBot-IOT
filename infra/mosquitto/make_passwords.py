"""Create MQTT passwords in .env and build the Mosquitto password file from them.

1. If .env does not exist, it is copied from .env.example.
2. Every MQTT_<USER>_PASS that is empty or still a "change-me" placeholder gets a strong random
   password (32 URL-safe characters, 192 bits). Existing real passwords are kept unless
   --rotate is given, which replaces all of them.
3. .local/mosquitto/passwd is rebuilt from the MQTT_<USER>_USER / MQTT_<USER>_PASS pairs with
   mosquitto_passwd. Plaintext is written to a temporary file on D: and hashed in place
   (mosquitto_passwd -U), so passwords never appear on a command line.

Passwords are never printed. .env and .local/ are git-ignored. Restart the broker afterwards
(or send it a reload) so it reads the new password file.

Usage (from the repo root):
  infra\\.venv\\Scripts\\python infra\\mosquitto\\make_passwords.py [--rotate]

Environment: FLYBOT_MOSQUITTO_DIR (default D:\\Tools\\mosquitto) locates mosquitto_passwd.exe.
Standard library only.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV_FILE = REPO / ".env"
ENV_EXAMPLE = REPO / ".env.example"
RUN_DIR = REPO / ".local" / "mosquitto"
PASSWD = RUN_DIR / "passwd"
USERS = ["ESP32", "SIM", "BRAIN", "DASHBOARD", "BOT"]
LINE_RE = re.compile(r"^(MQTT_(%s)_PASS)=(.*)$" % "|".join(USERS))


def is_placeholder(value: str) -> bool:
    return value.strip() == "" or value.strip().startswith("change-me")


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def update_env(rotate: bool) -> list[str]:
    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        print("created .env from .env.example")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    changed = []
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if m and (rotate or is_placeholder(m.group(3))):
            lines[i] = f"{m.group(1)}={secrets.token_urlsafe(24)}"
            changed.append(m.group(2).lower())
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, ENV_FILE)
    return changed


def build_passwd() -> int:
    env = read_env(ENV_FILE)
    pairs = []
    for u in USERS:
        user, pw = env.get(f"MQTT_{u}_USER", ""), env.get(f"MQTT_{u}_PASS", "")
        if not user or is_placeholder(pw):
            raise SystemExit(f"MQTT_{u}_USER / MQTT_{u}_PASS missing or placeholder in .env")
        if ":" in user or any(c.isspace() for c in pw):
            raise SystemExit(f"invalid characters in MQTT_{u}_USER or MQTT_{u}_PASS")
        pairs.append((user, pw))

    tool = Path(os.environ.get("FLYBOT_MOSQUITTO_DIR", r"D:\Tools\mosquitto")) / "mosquitto_passwd.exe"
    if not tool.exists():
        raise SystemExit(f"{tool} not found (set FLYBOT_MOSQUITTO_DIR)")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUN_DIR / "passwd.building"
    try:
        tmp.write_text("".join(f"{u}:{p}\n" for u, p in pairs), encoding="utf-8", newline="\n")
        subprocess.run([str(tool), "-U", str(tmp)], check=True, capture_output=True)
        os.replace(tmp, PASSWD)
    finally:
        if tmp.exists():
            tmp.unlink()
    return len(pairs)


def main() -> int:
    rotate = "--rotate" in sys.argv
    changed = update_env(rotate)
    print("new passwords generated for: " + (", ".join(changed) if changed else "none (kept existing)"))
    n = build_passwd()
    print(f"wrote {PASSWD.relative_to(REPO)} with {n} users (hashed)")
    print("restart the broker so it reads the new password file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
