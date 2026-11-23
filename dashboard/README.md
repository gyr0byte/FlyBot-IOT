# dashboard/

Next.js + TypeScript web dashboard. Not yet created.

Connects to the broker over WebSockets (`mqtt.js`) as MQTT user `dashboard` and shows:

- Robot and brain online/offline (from the retained `status` and `brain/status` topics).
- Live sensor charts, robot state, hunger/fear gauges, battery, signal and round-trip time.
- Brain view: spike counts per neuron group, learned-weights heatmap, decision and "why".
- Controls: mode (auto / manual / estop), manual drive pad (`cmd/manual`), reward and
  punish, sleep/wake (`brain/sleep`), lesion switches and brain save/load/reset (`brain/control`,
  which only the dashboard may send).
- History pages read from the SQLite database.

Note: the browser holds the `dashboard` MQTT credentials, so the dashboard itself must sit
behind a login. See the security note in [`docs/message-contract.md`](../docs/message-contract.md).

Keep the npm cache on D: (see [`CLAUDE.md`](../CLAUDE.md)).
