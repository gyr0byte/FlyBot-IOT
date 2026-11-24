"""Logger command line (Phase 8). From the brain folder:

    .venv\\Scripts\\python -m flybot_brain.logger run                 log on its own MQTT connection (no brain)
    .venv\\Scripts\\python -m flybot_brain.logger export --out D:\\path  one CSV per table (--minutes N: recent only)
    .venv\\Scripts\\python -m flybot_brain.logger prune --days 7       drop raw sensor/command/state rows older than 7 days
    .venv\\Scripts\\python -m flybot_brain.logger stats                row counts and reliability (invalid / lost)
    .venv\\Scripts\\python -m flybot_brain.logger api --port 8000     read-only HTTP API for the dashboard

Normally the logger runs inside the brain program (python -m flybot_brain), on the brain's connection.
The database is FLYBOT_DB_PATH from .env (default data/flybot.db, on D:).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from flybot_contract import TOPIC_SCHEMA, Contract

from ..config import MqttSettings, db_path_from_env
from . import tools
from .core import Logger

log = logging.getLogger("flybot_brain.logger")


def run_standalone(db_path: str) -> int:
    settings = MqttSettings.from_env()
    contract = Contract(settings.robot_id)
    logger = Logger(contract, db_path)
    logger.start()
    c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{settings.robot_id}-logger",
                    protocol=mqtt.MQTTv311, clean_session=True)
    c.username_pw_set(settings.user, settings.password)
    c.reconnect_delay_set(min_delay=1, max_delay=10)
    c.on_connect = lambda cl, *a: cl.subscribe(contract.prefix + "#", qos=1)

    def on_message(cl, userdata, m):
        name = contract.name_of(m.topic)
        if name in TOPIC_SCHEMA:
            logger.tap(name, m.payload, bool(m.retain))
    c.on_message = on_message
    c.connect_async(settings.host, settings.port, keepalive=10)
    c.loop_start()
    log.info("standalone logger running (Ctrl+C to stop)")
    stop = threading.Event()
    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        c.disconnect()
        c.loop_stop()
        logger.stop()
        log.info("stopped: %s", json.dumps(logger.reliability()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m flybot_brain.logger", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database file (default FLYBOT_DB_PATH from .env)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--minutes", type=float)
    p = sub.add_parser("prune")
    p.add_argument("--days", type=float, default=7.0)
    sub.add_parser("stats")
    a = sub.add_parser("api")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    db = args.db or db_path_from_env()
    if args.cmd == "run":
        return run_standalone(db)
    if args.cmd == "export":
        n = tools.export_csv(db, args.out, None if args.minutes is None else args.minutes * 60)
        print(json.dumps(n))
    elif args.cmd == "prune":
        print(json.dumps(tools.prune(db, args.days)))
    elif args.cmd == "stats":
        print(json.dumps(tools.stats(db), indent=1))
    elif args.cmd == "api":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(db), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
