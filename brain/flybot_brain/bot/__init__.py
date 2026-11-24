"""Telegram bot module (Phase 10): its OWN MQTT connection as user "bot" (narrow ACL: cmd/mode, reward,
brain/sleep), answers only TELEGRAM_ALLOWED_CHAT_ID, sends alerts from status, events, brain/status and
brain/state. Logic in core.Bot (no I/O), transport in telegram.py, wiring in runner.py.
"""
