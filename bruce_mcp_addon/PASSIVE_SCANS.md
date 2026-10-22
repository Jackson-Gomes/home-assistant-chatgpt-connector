# Passive signal discovery via Bruce MCP

The installed BruceRemote firmware advertises `ble.scan`, `wifi.scan` and `rf.scan` in `remote.capabilities`. The first two were absent from the MCP tool registry and MQTT transport allowlist; this branch exposes **discovery-only tools**.

## New MCP tools

- `bruce_ble_scan()` — runs the firmware `ble.scan` command and relays the firmware response, including identifiers/RSSI when provided.
- `bruce_wifi_scan()` — runs `wifi.scan` and relays SSID, channel and RSSI details when provided.

Both are single-shot with a 22-second acknowledgment timeout. They do not automatically run together or repeat in a loop. They do not pair, associate, alter devices, or inject attack traffic. Wi-Fi scans may send normal probe requests, and active BLE scans may send BLE scan requests, depending on firmware behavior.

## RF safety and limitations

`bruce_rf_scan(frequency_hz)` already exists, limited to 315.000 / 433.920 / 868.350 / 915.000 MHz. **Do not automatically include it in BLE/Wi-Fi surveys.** An observed `rf.scan` invocation at 433.920 MHz timed out and coincided with a device restart; the crash mechanism is unconfirmed. Investigate firmware resets/watchdogs and the CC1101 driver before attempting RF scan again.

`bruce_rf_receive`, `bruce_ir_receive`, and approved saved-signal transmitters remain unchanged, including their physical-unlock requirements.

## Deployment

1. Review and merge this branch.
2. Rebuild/update the Bruce MCP Connector Home Assistant add-on, then restart that add-on.
3. Refresh/reconnect the ChatGPT Bruce connector so the newly advertised tools appear.
4. Confirm online status and capability advertisement first. Invoke only **one** passive scan at a time; review device response, power and logs before further scans.

The MQTT transport currently ignores replies over 2048 bytes. If a scan finds many devices, the firmware needs to cap the result size or the protocol needs chunked paging; this change does not assume large payload delivery.

The firmware already advertises the scan operations. This branch does not change the ESP32 firmware. If its `ble.scan` / `wifi.scan` handlers fail or return no fields, update the device firmware separately.
