# BruceRemote MQTT wire protocol — v0.2

Transport: mutually protected broker connection using TLS/CA validation and distinct device and gateway accounts. The broker must be reachable from any user-authorized Wi-Fi network. Configure ACL to `bruce/v1/<device_id>/#` only for this device.

| Topic | Direction | Retain |
|---|---|---|
| `bruce/v1/<id>/cmd` | gateway to ESP32 | false |
| `bruce/v1/<id>/reply` | ESP32 to gateway | false |
| `bruce/v1/<id>/screen` | ESP32 to gateway (chunk stream) | false |
| `bruce/v1/<id>/ota` | ESP32 to gateway (progress) | false |
| `bruce/v1/<id>/status` | ESP32 to gateway (availability/LWT) | true |

Command envelope is JSON: `{"v":1,"id":"req-12345678","op":"info.get","args":{},"expires":<unix_utc_seconds>}`. Expires must be in the future, at most 120 seconds ahead, and request IDs must be unique. The device remembers recent IDs to mitigate replay and caps burst rates.

`info.get` reports Wi-Fi, RAM, PSRAM, brightness and uptime. `remote.capabilities` lists operations. `ota.status` reports active and next partition. `remote.unlock` must be invoked while physically holding the encoder (five-minute window) and `remote.lock` disables it.

`screen.snapshot` requires the unlock; it immediately replies with the number of chunks and CRC32, then publishes a stream with `{v,id,format:"bruce-tft-drawlog-v1",part,total,bytes,crc32,data:"base64..."}`. Reconstruct by ordering parts and verifying CRC32. This is a drawing-command log, **not** raw screenshot pixels. The local gateway exposes the decoded render via `/viewer` and `screen.base64` from the command response.

`ir.receive` and `rf.receive` / `rf.scan` return bounded decoded result strings via the normal `reply` topic. They require unlock and only support explicitly allowed frequencies. Capture return paths do not enable rebroadcast. `ir.send_saved` and `rf.send_saved` transmit only preconfigured local aliases and require unlock.

`ota.apply` expects `{"sha256":"<64-lowercase-hex>"}` in args and requires **both** unlocked state and encoder physically held at initiation. It only downloads from a locally pinned HTTPS URL verified with a locally installed CA PEM. The update is rejected on hash mismatch, error or oversize. It publishes JSON progress and final status on `/ota`.

Queued Bruce CLI commands report `queued_not_executed`, not completion; other operations return actual parsed/captured/validated results. Never expose raw CLI, arbitrary script execution, unauthorized BadUSB or wireless attack functions as remote operations. The gateway must stay local-only; ChatGPT MCP access requires a separate hardened connector.
