"""Logger module (Phase 8): every FlyBot message -> validated -> SQLite (core.Logger), CSV export and
retention (tools), read-only HTTP API for the dashboard (api). Runs inside the brain program on the
brain's MQTT connection (link taps) or standalone: python -m flybot_brain.logger run.
"""
