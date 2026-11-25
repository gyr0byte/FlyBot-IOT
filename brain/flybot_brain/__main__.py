"""FlyBot brain program. From the brain folder:

    .venv\\Scripts\\python -m flybot_brain                     (config/brain.yaml)
    .venv\\Scripts\\python -m flybot_brain --reset-on-reboot   (independent batch trials)
    .venv\\Scripts\\python -m flybot_brain --controller neural --config other.yaml
    .venv\\Scripts\\python -m flybot_brain --controller neural --no-persistence   (a naive animal)

One program, separate modules: the brain (this phase), the logger (Phase 8) and the Telegram
bot (Phase 10) will run here side by side, the logger on the brain's MQTT connection and the bot
on its own connection as user "bot". Ctrl+C stops it cleanly (saves snapshot "latest").
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from flybot_contract import Contract

from .config import MqttSettings, load_config
from .core import Brain
from .link import BrainLink
from .persistence import Snapshots

log = logging.getLogger("flybot_brain")


def build(args) -> tuple[Brain, BrainLink]:
    cfg = load_config(args.config)
    if args.controller:
        cfg.controller = args.controller
    if args.reset_on_reboot:
        cfg.experiment.reset_on_reboot = True
    if args.seed is not None:
        cfg.seed = args.seed
    if args.no_persistence:
        cfg.persistence.load_on_start = cfg.persistence.save_on_exit = False
    if args.restore_lesions:
        cfg.persistence.restore_lesions = True
    settings = MqttSettings.from_env()
    contract = Contract(settings.robot_id)
    brain = Brain(cfg, contract, Snapshots(cfg.persistence.path()))
    if cfg.persistence.load_on_start and brain.load("latest"):
        log.info("loaded snapshot 'latest'")
    if args.no_logger:
        cfg.logger.enabled = False
    for target in args.lesion or []:
        if not brain.set_lesion(target, True):
            raise SystemExit(f"lesion {target!r} is not available with controller {cfg.controller}")
        log.warning("started with lesion %s", target)
    return brain, BrainLink(settings, contract, brain)


def start_logger(cfg, link):
    """Phase 8: the logger module on the brain's connection (it sees every message the link gets)."""
    if not cfg.logger.enabled:
        return None
    from .config import db_path_from_env
    from .logger import tools
    from .logger.core import Logger
    path = db_path_from_env()
    try:
        tools.prune(path, cfg.logger.retention_days) if Path(path).exists() else None
    except Exception as e:                       # pruning must never stop the brain
        log.warning("logger: prune failed: %s", e)
    logger = Logger(link.contract, path)
    try:
        logger.start()
    except Exception as e:                       # e.g. an unusable database path: run without logging
        log.error("logger not started (%s: %s); the brain runs without it", type(e).__name__, e)
        return None
    link.taps.append(logger.tap)
    return logger


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m flybot_brain", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="YAML config (default brain/config/brain.yaml)")
    ap.add_argument("--controller", choices=["baseline", "neural"], help="override the config's controller")
    ap.add_argument("--reset-on-reboot", action="store_true",
                    help="reset internal state whenever the robot reboots (independent trials)")
    ap.add_argument("--seed", type=int, help="override the config's random seed")
    ap.add_argument("--lesion", action="append", choices=["rcpt", "ln", "mb", "apl", "learn", "hun", "fear"],
                    help="start with this group / function switched off (repeatable; e.g. lesion batches)")
    ap.add_argument("--no-bot", action="store_true",
                    help="do not start the Telegram bot even if a token is configured")
    ap.add_argument("--no-logger", action="store_true",
                    help="do not record messages into the database (experiment tools use this)")
    ap.add_argument("--restore-lesions", action="store_true",
                    help="when loading a snapshot, also re-apply the lesions it was saved with (off by default)")
    ap.add_argument("--no-persistence", action="store_true",
                    help="neither load nor save snapshot 'latest' (a fresh, naive brain: one experiment animal)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    brain, link = build(args)
    cfg = brain.cfg
    logger = start_logger(cfg, link)
    bot = None
    if not args.no_bot:
        from .bot.runner import start_bot
        bot = start_bot(link.contract)
    log.info("controller %s, reset_on_reboot %s, seed %s, load snapshot %s, save snapshot %s", cfg.controller,
             cfg.experiment.reset_on_reboot, cfg.seed, cfg.persistence.load_on_start, cfg.persistence.save_on_exit)
    if not link.start():
        log.warning("broker not reachable yet; will keep retrying (is it running?)")
    stop = threading.Event()
    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if cfg.persistence.save_on_exit and brain.save("latest"):
            log.info("saved snapshot 'latest'")
        link.stop()
        if bot is not None:
            bot.stop()
        if logger is not None:
            logger.stop()
            log.info("logger: %d messages written", logger.written)
        log.info("stopped: %s", brain.counters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
