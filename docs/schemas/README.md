# docs/schemas/

One JSON Schema (draft 2020-12) per MQTT message type. These are the machine-checkable
form of [`../message-contract.md`](../message-contract.md); if the two disagree, that is a bug.

- **Strict:** unknown fields are rejected (`additionalProperties: false`, or
  `unevaluatedProperties: false` where a schema has variants).
- **Self-contained:** no `$ref` between files, so Python and TypeScript validators need no
  extra setup.
- **Examples included:** each schema's `examples` keyword holds the same example message as
  the contract.
- **Versioned:** each message's `v` field is pinned with `const`. Changing a payload means
  bumping `v` here and in the contract in the same commit.

| Schema | Topic |
|---|---|
| `sensors.schema.json` | `flybot/01/sensors` |
| `events.schema.json` | `flybot/01/events` |
| `status.schema.json` | `flybot/01/status` |
| `cmd-motor.schema.json` | `flybot/01/cmd/motor` and `flybot/01/cmd/manual` (same payload) |
| `cmd-mode.schema.json` | `flybot/01/cmd/mode` |
| `cmd-sleep.schema.json` | `flybot/01/cmd/sleep` |
| `reward.schema.json` | `flybot/01/reward` |
| `brain-status.schema.json` | `flybot/01/brain/status` |
| `brain-state.schema.json` | `flybot/01/brain/state` |
| `brain-weights.schema.json` | `flybot/01/brain/weights` |
| `brain-control.schema.json` | `flybot/01/brain/control` |
| `ping.schema.json` | `flybot/01/ping` |
| `pong.schema.json` | `flybot/01/pong` |
