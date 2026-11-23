"""Logger module (Phase 8): not built yet.

Will subscribe through the brain's MQTT connection (user "brain" reads flybot/01/#), validate
every message with flybot_contract and write it to SQLite, using `up` (not arrival time) to place
messages from fast lockstep runs on a timeline (contract 7.1 (6)).
"""
