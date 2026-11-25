"""Runs the Telegram bot: its own MQTT connection as user "bot" (contract section 4) plus Telegram long
polling. Started by the brain program when a bot token is configured, or alone: python -m flybot_brain.bot
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time

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
    """Failures are contained: a Telegram error makes the poller back off (1 s -> 60 s) and retry; a
    message is sent up to `send_tries` times; an exception in the bot logic or an MQTT callback is
    logged (rate-limited) and skipped. Nothing here can stop the brain (it runs on its own threads and
    its own MQTT connection)."""

    def __init__(self, settings: MqttSettings, contract: Contract, bot: Bot, telegram: TelegramAPI,
                 client: mqtt.Client | None = None, poll_timeout_s: int = 25,
                 backoff_s: tuple[float, float] = (1.0, 60.0), send_tries: int = 3):
        self.settings, self.contract, self.bot, self.telegram = settings, contract, bot, telegram
        self.poll_timeout_s = poll_timeout_s
        self.backoff_s, self.send_tries = backoff_s, send_tries
        self.errors = 0
        self._last_error_log = 0.0
        self._stop = threading.Event()
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
        self._stop.set()
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

    def _failed(self, what: str, e: Exception) -> None:
        self.errors += 1
        now = time.monotonic()
        if now - self._last_error_log > 10.0:
            self._last_error_log = now
            log.error("bot %s failed (%s: %s); %d errors so far; carrying on", what, type(e).__name__, e, self.errors)

    def _on_message(self, client, userdata, m):
        try:
            name = self.contract.name_of(m.topic)
            if name not in SUBSCRIBE or not m.payload:
                return
            try:
                msg = self.contract.decode(name, m.payload)
            except ContractError:
                return
            for r in self.bot.on_mqtt(name, msg, bool(m.retain)):
                self.outq.put(r)
        except Exception as e:                        # never break the bot's MQTT thread
            self._failed("message handling", e)

    def publish(self, p: Publish) -> None:
        self.client.publish(self.contract.topic(p.name), self.contract.encode(p.name, p.msg), qos=p.qos, retain=p.retain)

    # ------------------------------------------------------------------ Telegram

    def handle_update(self, chat_id: int, text: str) -> None:
        if chat_id not in self.bot.cfg.allowed_chats:
            log.warning("ignored a message from chat %s (not allowed)", chat_id)
            return
        try:
            replies, pubs = self.bot.on_text(chat_id, text)
            for p in pubs:
                self.publish(p)
        except Exception as e:
            self._failed(f"command {text.split()[0] if text.split() else ''!r}", e)
            replies = [Reply(chat_id, "Sorry, that failed; it has been logged.")]
        for r in replies:
            self.outq.put(r)

    def poll_once(self) -> bool:
        """One getUpdates round. False if Telegram could not be reached (the caller backs off)."""
        try:
            updates = self.telegram.get_updates(self.offset, self.poll_timeout_s)
        except Exception as e:
            self._failed("telegram poll", e)
            return False
        for u in updates:
            self.offset = u.update_id + 1
            if u.text:
                self.handle_update(u.chat_id, u.text)
        return True

    def _poll(self) -> None:
        backoff = self.backoff_s[0]
        while self.running:
            if self.poll_once():
                backoff = self.backoff_s[0]
            else:
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.backoff_s[1])

    def send_once(self, r: Reply) -> bool:
        delay = self.backoff_s[0]
        for attempt in range(self.send_tries):
            try:
                if self.telegram.send_message(r.chat_id, r.text):
                    return True
            except Exception as e:
                self._failed("telegram send", e)
            if attempt + 1 < self.send_tries and not self._stop.wait(delay):
                delay = min(delay * 2, self.backoff_s[1])
        log.warning("dropped a message to chat %s after %d tries", r.chat_id, self.send_tries)
        return False

    def _send(self) -> None:
        while self.running:
            r = self.outq.get()
            if isinstance(r, Reply):
                self.send_once(r)


def start_bot(contract: Contract) -> BotRunner | None:
    """Start the bot if a token and allowed chat ID are configured (.env), else return None. Any
    failure while starting is logged and the brain runs on without the bot."""
    try:
        return _start_bot(contract)
    except Exception as e:
        log.error("telegram bot not started (%s: %s); the brain runs without it", type(e).__name__, e)
        return None


def _start_bot(contract: Contract) -> BotRunner | None:
    conf = bot_settings()
    if conf is None:
        log.info("telegram bot not started: TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not configured")
        return None
    token, chats = conf
    runner = BotRunner(MqttSettings.from_env(user_key="BOT"), contract, Bot(contract, BotConfig(chats)),
                       HttpTelegram(token))
    runner.start()
    return runner
