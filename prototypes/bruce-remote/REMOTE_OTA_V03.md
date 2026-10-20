# BruceRemote v0.3 — Remote diagnostics and HTTPS OTA

This branch contains Bruce firmware source and the standalone ChatGPT MCP connector. A firmware build does **not** change the device that is currently running. Do not push the factory/merged image via OTA.

## One-time setup (can be performed with the Bruce WebUI, no USB if the device already has the dual-OTA layout)

From the locally authenticated Bruce WebUI, edit **/bruce-remote.json** and set:

```json
"remote": {"menu": true, "passive_scans": true},
"ota": {
  "enabled": true,
  "allow_remote_apply": true,
  "url": "https://raw.githubusercontent.com/Jackson-Gomes/home-assistant-chatgpt-connector/bruce-ota-images/firmware.bin",
  "ca_path": "/bruce-ota-ca.pem"
}
```

The example above is a JSON fragment inside the already existing configuration file. Keep your device-specific MQTT credentials and CA untouched. Install the HTTPS server's trusted root PEM at `/bruce-ota-ca.pem` (the MQTT broker CA may be different). Restart Bruce after editing.

These options are **false by default** if omitted. Never turn on unattended OTA for an unauthenticated MQTT broker or an unsecured MCP tunnel.

### How future chat updates work

1. A commit on `feature/bruce-standalone-mcp` starts the **Bruce T-Embed CC1101 Remote OTA** build workflow.
2. If the build and tests succeed, the workflow publishes the **application-only** image, SHA-256 and upstream commit to `bruce-ota-images`.
3. Read `ota-app-sha256.txt` from that branch. The hash on its first line belongs to `firmware.bin` (not to the USB/factory image).
4. Check `bruce_ota_status`; verify `configured=true` and `dual_partition=true`. Ask for explicit confirmation before flashing.
5. Call `bruce_ota_apply(sha256, confirm=true)`. The device uses the **locally pinned URL**, validates the HTTPS certificate and SHA-256 before switching the inactive app partition.
6. Verify `bruce_info` / `bruce_capabilities` after reconnect; an OTA start response by itself is not success.

**Bootstrap limitation:** older firmware still requires holding the encoder for its original OTA command. If the existing build has no dual OTA partition, one USB full-flash migration is unavoidable. A firmware patch in GitHub cannot retroactively bypass the old firmware's checks.

### Remote diagnostics

- `bruce_wifi_scan`: listens for access-point beacons (passive scan), up to 10 results with SSID, RSSI, channel, BSSID and auth code.
- `bruce_ble_scan`: short passive BLE advertisement scan, up to 10 address/name/RSSI results (BLE only, not classic Bluetooth).
- `bruce_rf_scan`, `bruce_rf_receive`, `bruce_ir_receive`: bounded receive-only captures on approved bands without requiring a pressed encoder.
- `bruce_navigate`, `bruce_menu_option`, `bruce_screen_snapshot`: available only after local opt-in. Menu control can itself reach powerful built-in Bruce features; enable it only on an owner-controlled device.
- `bruce_ir_send_saved` and `bruce_rf_send_saved`: retain their original physical unlock requirement. Arbitrary radio transmission, script execution and raw serial commands are not exposed over MCP.

**Security:** MQTT TLS and per-device credentials, authenticated MCP tunnel, device-local opt-in, pinning OTA URL/CA, explicit confirm, hash verification. This does not cryptographically sign releases. Preserve a known-good USB factory image for recovery. BLE/Wi-Fi scans can momentarily affect Wi-Fi connectivity; verify behavior on hardware.
