"""Runs the Telegram bot: its own MQTT connection as user "bot" (contract section 4) plus Telegram long
polling. Started by the brain program when a bot token is configured, or alone: python -m flybot_brain.bot
"""
from __future__ import annotations

import logging
import queue
import re
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from flybot_contract import Contract, ContractError

from ..config import MqttSettings, read_env
from .core import SUBSCRIBE, Bot, BotConfig, Publish, Reply
from .telegram import HttpTelegram, TelegramAPI

log = logging.getLogger("flybot_brain.bot")
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def bot_settings(env: dict[str, str] | None = None) -> tuple[str, frozenset[int]] | None:
    """(token, allowed chat IDs) if a real-looking token and at least one chat ID are configured.
    TELEGRAM_ALLOWED_CHAT_ID may list several IDs separated by commas."""
    try:
        env = env if env is not None else read_env()
    except FileNotFoundError:
        return None
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = set()
    for part in env.get("TELEGRAM_ALLOWED_CHAT_ID", "").split(","):
        part = part.strip()
        if re.fullmatch(r"-?\d+", part) and int(part) != 0:
            chats.add(int(part))
    if not TOKEN_RE.match(token) or not chats:
        return None
    return token, frozenset(chats)


class BotRunner:
    def __init__(self, settings: MqttSettings, contract: Contract, bot: Bot, telegram: TelegramAPI,
                 client: mqtt.Client | None = None, poll_timeout_s: int = 25):
        self.settings, self.contract, self.bot, self.telegram = settings, contract, bot, telegram
        self.poll_timeout_s = poll_timeout_s
        self.outq: queue.Queue = queue.Queue()
        self.running = False
        self.offset: int | None = None
        self.threads: list[threading.Thread] = []
        self.client = client or mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-{settings.robot_id}-bot",
                                            protocol=mqtt.MQTTv311, clean_session=True)
        self.client.username_pw_set(settings.user, settings.password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.running = True
        self.client.connect_async(self.settings.host, self.settings.port, keepalive=30)
        self.client.loop_start()
        for target, name in ((self._poll, "bot-poll"), (self._send, "bot-send")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self.threads.append(t)
        log.info("telegram bot running for %d chat(s)", len(self.bot.cfg.allowed_chats))

    def stop(self) -> None:
        self.running = False
        self.outq.put(None)
        self.client.disconnect()
        self.client.loop_stop()

    # ------------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            log.error("broker refused the bot: %s", reason_code)
            return
        for name in SUBSCRIBE:
            client.subscribe(self.contract.topic(name), qos=1)

    def _on_message(self, client, userdata, m):
        name = self.contract.name_of(m.topic)
        if name not in SUBSCRIBE or not m.payload:
            return
        try:
            msg = self.contract.decode(name, m.payload)
        except ContractError:
            return
        for r in self.bot.on_mqtt(name, msg, bool(m.retain)):
            self.outq.put(r)

    def publish(self, p: Publish) -> None:
        self.client.publish(self.contract.topic(p.name), self.contract.encode(p.name, p.msg), qos=p.qos, retain=p.retain)

    # ------------------------------------------------------------------ Telegram

    def handle_update(self, chat_id: int, text: str) -> None:
        if chat_id not in self.bot.cfg.allowed_chats:
            log.warning("ignored a message from chat %s (not allowed)", chat_id)
            return
        replies, pubs = self.bot.on_text(chat_id, text)
        for p in pubs:
            self.publish(p)
        for r in replies:
            self.outq.put(r)

    def _poll(self) -> None:
        while self.running:
            for u in self.telegram.get_updates(self.offset, self.poll_timeout_s):
                self.offset = u.update_id + 1
                if u.text:
                    self.handle_update(u.chat_id, u.text)

    def _send(self) -> None:
        while self.running:
            r = self.outq.get()
            if isinstance(r, Reply):
                self.telegram.send_message(r.chat_id, r.text)


def start_bot(contract: Contract) -> BotRunner | None:
    """Start the bot if a token and allowed chat ID are configured (.env), else return None."""
    conf = bot_settings()
    if conf is None:
        log.info("telegram bot not started: TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not configured")
        return None
    token, chats = conf
    runner = BotRunner(MqttSettings.from_env(user_key="BOT"), contract, Bot(contract, BotConfig(chats)),
                       HttpTelegram(token))
    runner.start()
    return runner
