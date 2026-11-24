"""Telegram Bot API client: the real one (HTTPS, long polling, standard library only) and a fake for tests.

Only two calls are used: getUpdates (long polling) and sendMessage. The token is never logged.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("flybot_brain.bot")


@dataclass
class Update:
    update_id: int
    chat_id: int
    text: str
    user: str = ""


class TelegramAPI:
    def get_updates(self, offset: int | None, timeout_s: int) -> list[Update]:
        raise NotImplementedError

    def send_message(self, chat_id: int, text: str) -> bool:
        raise NotImplementedError


class HttpTelegram(TelegramAPI):
    BASE = "https://api.telegram.org"

    def __init__(self, token: str):
        self._url = f"{self.BASE}/bot{token}/"

    def _call(self, method: str, params: dict, timeout_s: float) -> dict | None:
        data = urllib.parse.urlencode(params).encode()
        try:
            with urllib.request.urlopen(self._url + method, data=data, timeout=timeout_s) as r:
                body = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            log.warning("telegram %s failed: %s", method, type(e).__name__)      # no URL: it holds the token
            return None
        if not body.get("ok"):
            log.warning("telegram %s refused: %s", method, body.get("description", "?"))
            return None
        return body

    def get_updates(self, offset: int | None, timeout_s: int) -> list[Update]:
        params = {"timeout": timeout_s, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        body = self._call("getUpdates", params, timeout_s + 10)
        out = []
        for u in (body or {}).get("result", []):
            m = u.get("message") or {}
            if "text" in m and "chat" in m:
                out.append(Update(u["update_id"], int(m["chat"]["id"]), m["text"],
                                  (m.get("from") or {}).get("username", "")))
            else:
                out.append(Update(u["update_id"], 0, ""))       # still consume it
        return out

    def send_message(self, chat_id: int, text: str) -> bool:
        return self._call("sendMessage", {"chat_id": chat_id, "text": text[:4000]}, 15) is not None


@dataclass
class FakeTelegram(TelegramAPI):
    """In-memory Telegram for tests: queue incoming texts, read what the bot sent."""
    incoming: list[Update] = field(default_factory=list)
    sent: list[tuple[int, str]] = field(default_factory=list)
    _next_id: int = 1

    def say(self, chat_id: int, text: str) -> None:
        self.incoming.append(Update(self._next_id, chat_id, text))
        self._next_id += 1

    def get_updates(self, offset: int | None, timeout_s: int) -> list[Update]:
        out = [u for u in self.incoming if offset is None or u.update_id >= offset]
        self.incoming = []
        return out

    def send_message(self, chat_id: int, text: str) -> bool:
        self.sent.append((chat_id, text))
        return True
