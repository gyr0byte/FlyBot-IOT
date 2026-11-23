"""Test driver for the simulator (or the real robot) until the brain exists.

    sim\\.venv\\Scripts\\python sim\\tools\\drive.py straight
    sim\\.venv\\Scripts\\python sim\\tools\\drive.py "straight:3,turn:2,silent:2,straight:3" --loop

Connects with the BRAIN credentials from .env: never run it while the real brain is running.
Answers each sensors message (so it also drives lockstep runs) and every ping. Ctrl+C stops.
See flybot_sim/driver.py for the actions.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flybot_sim.config import MqttSettings  # noqa: E402
from flybot_sim.driver import ACTIONS, Driver  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help=f"one action ({', '.join(ACTIONS)}) or segments like 'straight:3,silent:2'")
    ap.add_argument("--loop", action="store_true", help="repeat the script")
    ap.add_argument("--ttl", type=int, default=300, help="cmd/motor time-to-live in ms (default 300)")
    ap.add_argument("--no-wait-boot", action="store_true", help="start the script before odour warm-up ends")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    d = Driver(MqttSettings.from_env("BRAIN"), args.script, loop=args.loop,
               wait_boot=not args.no_wait_boot, ttl=args.ttl)
    d.start()
    logging.info("test driver running as user 'brain' (Ctrl+C to stop)")
    try:
        while not d.done.is_set():
            time.sleep(0.2)
        logging.info("script finished: %d commands sent; now silent", d.sent)
    except KeyboardInterrupt:
        pass
    finally:
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
