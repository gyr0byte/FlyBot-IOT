"""Run the Telegram bot on its own (normally it runs inside python -m flybot_brain):

    .venv\Scripts\python -m flybot_brain.bot
"""
from __future__ import annotations

import logging
import sys
import threading

from flybot_contract import Contract

from ..config import MqttSettings
from .runner import start_bot


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    runner = start_bot(Contract(MqttSettings.from_env(user_key="BOT").robot_id))
    if runner is None:
        return 1
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
