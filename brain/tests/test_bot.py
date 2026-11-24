"""Telegram bot (Phase 10): commands, alerts, access control, runner; with a fake Telegram."""
from __future__ import annotations

import types

import pytest

from flybot_brain.bot.core import Bot, BotConfig, Publish, Reply
from flybot_brain.bot.runner import BotRunner, bot_settings
from flybot_brain.bot.telegram import FakeTelegram
from flybot_brain.config import MqttSettings
from flybot_contract import Contract

C = Contract()
ME, FRIEND, STRANGER = 111, 222, 999
BID = 2847193551


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**cfg):
    clock = Clock()
    return Bot(C, BotConfig(frozenset({ME, FRIEND}), **cfg), clock=clock), clock


def status(st="cloud", up=3_723_000, seq=1, bat=7.6):
    if st == "offline":
        return {"v": 3, "seq": 0, "up": 100, "bid": BID, "st": "offline"}
    return {"v": 3, "seq": seq, "up": up, "bid": BID, "st": st, "m": "auto", "rssi": -58, "heap": 1, "bat": bat,
            "rtt": 12.0, "crtt": 20.0, "fw": "0.1.0", "hab": 0.1}


def brain_status(st="online", lag=0):
    if st == "offline":
        return {"v": 3, "seq": 0, "up": 1, "st": "offline"}
    return {"v": 3, "seq": 1, "up": 5000, "st": "online", "ctrl": "neural", "tick": 5.0, "ovr": 0, "sens": "ok", "lag": lag}


def brain_state(pref=None, **kw):
    m = {"v": 5, "seq": 1, "up": 6000, "hun": 0.42, "fear": 0.1, "hab": 0.2, "slp": False, "dec": "seek",
         "why": ["odour_l"], "spk": {}, "les": [], "pref": pref or {}, "rs": 7, "ack": None}
    m.update(kw)
    return m


def event(e, **f):
    return {"v": 3, "seq": 1, "up": 1, "bid": BID, "e": e, **f}


def texts(replies):
    return [r.text for r in replies]


# ----------------------------------------------------------------------------- commands

@pytest.mark.parametrize("cmd, name, field", [("/good", "reward", ("r", 1)), ("/bad", "reward", ("r", -1)),
                                              ("/sleep", "brain/sleep", ("s", True)), ("/wake", "brain/sleep", ("s", False)),
                                              ("/stop", "cmd/mode", ("m", "estop")), ("/resume", "cmd/mode", ("m", "auto"))])
def test_commands_publish_contract_valid_messages(cmd, name, field):
    bot, _ = make()
    replies, pubs = bot.on_text(ME, cmd)
    assert len(replies) == 1 and replies[0].chat_id == ME
    (p,) = pubs
    C.validate(p.name, p.msg)
    assert p.name == name and p.msg[field[0]] == field[1] and p.msg["src"] == "bot"
    assert p.retain == (name == "cmd/mode") and p.qos == 1


def test_seq_counts_per_topic():
    bot, _ = make()
    seqs = [bot.on_text(ME, "/good")[1][0].msg["seq"] for _ in range(3)] + [bot.on_text(ME, "/sleep")[1][0].msg["seq"]]
    assert seqs == [0, 1, 2, 0]


def test_strangers_are_ignored():
    bot, _ = make()
    assert bot.on_text(STRANGER, "/stop") == ([], [])


def test_help_unknown_and_bot_suffix():
    bot, _ = make()
    assert "/status" in bot.on_text(ME, "/help")[0][0].text
    assert "Unknown command" in bot.on_text(ME, "/dance")[0][0].text
    assert bot.on_text(ME, "/good@FlyBotBot")[1][0].msg["r"] == 1


def test_status_reports_state_hunger_fear_battery_uptime():
    bot, _ = make()
    assert "Robot: offline" in bot.status_text()
    bot.on_mqtt("status", status())
    bot.on_mqtt("brain/status", brain_status(lag=3))
    bot.on_mqtt("brain/state", brain_state(les=["mb"]))
    text = bot.on_text(ME, "/status")[0][0].text
    for part in ("cloud", "7.6 V", "up 1h02m", "Hunger 0.42", "fear 0.10", "habituation 0.20", "lag 3", "seek", "mb"):
        assert part in text, part


def test_learned_summary():
    bot, _ = make()
    assert "Nothing learned" in bot.on_text(ME, "/learned")[0][0].text
    bot.on_mqtt("brain/state", brain_state(pref={"lt": -0.62, "odr": 0.05}))
    text = bot.on_text(ME, "/learned")[0][0].text
    assert "the light: bad (-0.62)" in text and "the odour: neutral (+0.05)" in text


# ----------------------------------------------------------------------------- alerts

def test_robot_offline_and_online_alert_once_per_change():
    bot, _ = make()
    assert bot.on_mqtt("status", status(), retained=True) == []            # first sight: no alert
    assert bot.on_mqtt("status", status(seq=2)) == []
    off = bot.on_mqtt("status", status("offline"))
    assert texts(off) == ["🔴 Robot went offline."] * 2 and {r.chat_id for r in off} == {ME, FRIEND}
    assert bot.on_mqtt("status", status("offline")) == []
    assert texts(bot.on_mqtt("status", status(seq=3)))[0] == "🟢 Robot is back online."


def test_low_battery_and_fallback_alerts():
    bot, _ = make()
    assert "low: 6.70 V" in bot.on_mqtt("events", event("low_battery", bat=6.7, cut=False))[0].text
    assert "cut-off" in bot.on_mqtt("events", event("low_battery", bat=6.3, cut=True))[0].text
    assert "fallback (cmd timeout)" in bot.on_mqtt("events", event("fallback_on", why="cmd_timeout"))[0].text
    assert "ended after 12 s" in bot.on_mqtt("events", event("fallback_off", dur=12250))[0].text


def test_escape_alerts_are_rate_limited():
    bot, clock = make(escape_window_s=60)
    esc = event("escape", ttc=400, d=31.0, lat=2.0, resp=1.0, hab=0.0)
    assert len(bot.on_mqtt("events", esc)) == 2                            # one per chat
    for _ in range(4):
        clock.t += 5
        assert bot.on_mqtt("events", esc) == []
    clock.t += 60
    assert "+4 more" in bot.on_mqtt("events", esc)[0].text
    habituated = event("escape", ttc=400, d=31.0, resp=0.0, hab=0.9)
    clock.t += 120
    assert bot.on_mqtt("events", habituated) == []                         # detected but no escape: no alert


def test_brain_lag_and_brain_offline_alerts():
    bot, _ = make(lag_alert=40)
    assert bot.on_mqtt("brain/status", brain_status(lag=0)) == []
    assert "falling behind: 57" in bot.on_mqtt("brain/status", brain_status(lag=57))[0].text
    assert bot.on_mqtt("brain/status", brain_status(lag=80)) == []         # once
    assert "caught up" in bot.on_mqtt("brain/status", brain_status(lag=2))[0].text
    assert "Brain went offline" in bot.on_mqtt("brain/status", brain_status("offline"))[0].text


def test_learned_preference_alert_once_and_rearmed():
    bot, _ = make()
    assert bot.on_mqtt("brain/state", brain_state(pref={"lt": -0.3})) == []
    a = bot.on_mqtt("brain/state", brain_state(pref={"lt": -0.55}))
    assert texts(a)[0] == "🎓 Learned: the light = bad (-0.55)."
    assert bot.on_mqtt("brain/state", brain_state(pref={"lt": -0.8})) == []
    assert bot.on_mqtt("brain/state", brain_state(pref={"lt": -0.1})) == []     # re-armed below 0.25
    assert "good" in bot.on_mqtt("brain/state", brain_state(pref={"lt": 0.6}))[0].text


# ----------------------------------------------------------------------------- settings and runner

def test_bot_settings_need_a_real_token_and_a_chat():
    assert bot_settings({"TELEGRAM_BOT_TOKEN": "000000000:replace-with-token-from-botfather",
                         "TELEGRAM_ALLOWED_CHAT_ID": "000000000"}) is None
    real = "123456789:AAH" + "x" * 32
    assert bot_settings({"TELEGRAM_BOT_TOKEN": real, "TELEGRAM_ALLOWED_CHAT_ID": ""}) is None
    assert bot_settings({"TELEGRAM_BOT_TOKEN": real, "TELEGRAM_ALLOWED_CHAT_ID": "111, -222 ,x"}) == (real, frozenset({111, -222}))


class FakeMqtt:
    def __init__(self):
        self.published = []

    def username_pw_set(self, *a): pass
    def reconnect_delay_set(self, **k): pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def test_runner_end_to_end_with_fake_telegram_and_mqtt():
    tg, client = FakeTelegram(), FakeMqtt()
    bot, _ = make()
    r = BotRunner(MqttSettings("127.0.0.1", 1883, "bot", "x"), C, bot, tg, client=client)
    r.handle_update(ME, "/stop")
    r.handle_update(STRANGER, "/good")
    (topic, payload, qos, retain), = client.published
    assert topic == C.topic("cmd/mode") and retain and qos == 1 and C.decode("cmd/mode", payload)["m"] == "estop"
    msg = types.SimpleNamespace(topic=C.topic("events"), payload=C.encode("events", event("low_battery", bat=6.6, cut=False)),
                                retain=False)
    r._on_message(None, None, msg)
    bad = types.SimpleNamespace(topic=C.topic("events"), payload=b"garbage", retain=False)
    r._on_message(None, None, bad)                                          # invalid: ignored
    queued = []
    while not r.outq.empty():
        queued.append(r.outq.get())
    assert [q.chat_id for q in queued] == [ME, ME, FRIEND] and "Emergency stop" in queued[0].text
    assert "6.60 V" in queued[1].text
