# Bruce Connector Protocol (draft v0.1)

Transport: MQTT over TLS (default TCP 8883), broker reachable from multiple networks. Device must authenticate with its own least-privilege identity. Do not connect to unsecured public brokers.

Topic root: `bruce/v1/<device-id>`, where device-id is a user-defined random identifier (not MAC).

- `cmd` (QoS 1, retain=false): commands to device.
- `reply` (QoS 1, retain=false): result sent to connector.
- `status` (QoS 1, retained): small, sanitized online/offline state using last-will.
- `telemetry` (QoS 0, retain=false): sanitized metrics, optionally disabled.

Command:
```json
{"v":1,"id":"9b74987b-88eb-4f65-9d7b-b57d8ec3fa15","op":"info.get","args":{},"expires_at":"2026-10-19T19:00:00Z"}
```

Response:
```json
{"v":1,"id":"9b74987b-88eb-4f65-9d7b-b57d8ec3fa15","ok":true,"result":{"firmware":"Bruce","online":true}}
```

Initial allowlist:
- `info.get`: sanitized firmware/network/uptime/heap report.
- `settings.get_safe`: read safe user-approved settings.
- `ir.transmit_saved`: named allowlisted IR codes *for user-owned devices only*; requires confirmation if unknown.
- `device.restart`: explicit user confirmation.

Do **not** bridge arbitrary raw Bruce CLI or file/script execution. Do not expose `badusb`, Wi-Fi attacks, unauthorized RF capture/replay, or executable JS to remote tools.

Constraints: reject unknown ops and oversized payloads, validate JSON schema and UTF-8, reject expired messages and replays (bounded ID cache), cap request rates, send acknowledgments; no confidential details in logs. The ESP32 must remain usable locally when broker unreachable. Wi-Fi provisioning for new SSIDs must require physical user interaction and should not make an unauthenticated AP available indefinitely.

Lifecycle: test standalone official firmware build -> read-only MQTT client -> credential provisioning -> secure remote connector -> allowed IR commands -> optional OTA with signed artifacts and rollback. Initial build is **not** MQTT enabled.
