# BruceRemote v0.2 — Experimental USB, MQTT and OTA

This is an experimental **overlay** built against the latest BruceDevices/firmware source for the **LILYGO T-Embed CC1101 Plus (ESP32-S3 with 16 MB flash)**. The Home Assistant connector's production `main` branch remains untouched.

## First installation — USB and complete erase REQUIRED

The original Bruce firmware uses one factory app partition. This build changes the partition layout to two OTA partitions, each 0x470000 bytes, plus a 0x700000-byte LittleFS partition. **Do not perform a non-erasing update from the original Bruce!** An old filesystem or partition layout will not be compatible.

1. In GitHub Actions select the latest successful **Bruce T-Embed CC1101 Remote OTA** run on `feature/bruce-telemetry-ota`. Download its artifact ZIP.
2. Extract `Bruce-lilygo-t-embed-cc1101.bin`. This is the **USB factory image**, including bootloader, new partition table and application.
3. Use the official Espressif esptool-js flasher or esptool on USB. **Erase entire flash first**. Flash the combined image at offset `0x0`; then reset the device.
4. Verify the normal Bruce menu works. There is no need for a microSD.
5. Configure Wi-Fi from the Bruce menu, turn on Wi-Fi at Startup, and change the Bruce WebUI default password.
6. Configure your own **TLS MQTT broker** with device-specific username/password and topic ACL. Using the Bruce local WebUI, upload a *copy* of `bruce-remote.example.json` to LittleFS as `/bruce-remote.json`, with `enabled: true`, proper broker, port, ID and credentials. Upload the broker CA certificate as `/bruce-ca.pem`. Never commit the real config to GitHub.
7. Reboot. The connector should publish `bruce/v1/<device_id>/status`. The local gateway and ChatGPT connector are not active until separately configured.

## MQTT operations

Remote commands are JSON on `bruce/v1/<id>/cmd` (QoS 1, retain **false**) and replies on `/reply`.

Example:

```json
{"v":1,"id":"req-abcdef123456","op":"info.get","args":{},"expires":1792440000}
```

The epoch expiry must be a future UTC UNIX **seconds** timestamp, not an ISO string; max 120 seconds in the future. The gateway generates it at request time. The device rejects replayed or expired IDs. Do not publish retained commands.

Operation families:
- `info.get`, `remote.capabilities`, `ota.status`: safe read-only.
- `screen.brightness`, `screen.color`, `sound.tone`, `sound.say`: benign, limited inputs; CLI actions return **queued**, not execution completion.
- `remote.unlock`: hold the T-Embed encoder button physically while invoking; enables sensitive operations for **five minutes**.
- `remote.lock`: ends the temporary authorization.
- `menu.nav`, `menu.option`: physical unlock required; mirrors menu navigation, not arbitrary shell or code execution.
- `ir.send_saved`, `rf.send_saved`: physically unlocked, command aliases must be approved in the **local** config.
- `ir.receive`, `rf.receive`, `rf.scan`: physically unlocked; returns a bounded response with decoded data/scan results (not unlimited raw capture).
- `screen.snapshot`: physically unlocked, triggers a chunked stream to `/screen`, assembled by the gateway using CRC32. It mirrors **TFT drawing commands**, not a hardware framebuffer, and cannot reproduce images stored only on the T-Embed.
- `ota.apply`: requires physical unlock **and a currently held encoder button**, local OTA enabled, and the expected SHA-256 of the app-only binary. Progress and completion are published to `/ota`.

## Later OTA updates

GitHub Actions now builds two distinct files:
- `Bruce-lilygo-t-embed-cc1101.bin`: combined factory image for USB and offset `0x0`. **Never supply this to OTA.**
- `.pio/build/lilygo-t-embed-cc1101/firmware.bin`: **app-only** image for OTA.

To enable OTA in `/bruce-remote.json`:

```json
"ota": {
  "enabled": true,
  "url": "https://your-owned-host.example/firmware.bin",
  "ca_path": "/bruce-ota-ca.pem"
}
```

Upload the HTTPS server's trusted root CA PEM to `/bruce-ota-ca.pem`. Host the app-only binary at the **locally pinned** HTTPS URL. Check `ota-app-sha256.txt` in the GitHub artifact and send the 64 lowercase hex hash with `ota.apply`. The device streams the HTTPS response into the inactive OTA partition, computes SHA-256, and **aborts without switching partitions** when the hash differs. After successful verification/finalization, it reports success and reboots. A failed boot is recoverable by USB reflashing; automatic bootloader rollback is **not claimed** or enabled by this prototype. A full USB flash is required only to migrate from the old one-slot partition layout.

**Security:** TLS certificate validation, fixed local HTTPS URL, content hash, local physical button, and separate MQTT credentials. The hash protects integrity relative to the operator's expected artifact; this is **not a signed release system**. Do not enable OTA until the broker and gateway are secured.

## Gateway viewer

Run `gateway/server.py` with Python, the gateway dependencies and local TLS broker credentials as described in `gateway/README.md`. Open `http://127.0.0.1:8765/viewer`, paste the bearer token, and unlock via the T-Embed button. The viewer reconstructs the screen from the Bruce TFT logs. The gateway is **not an internet-facing MCP service**, and full ChatGPT integration remains a separate deployment task.

## Tests and limitations

The build and Python contract tests run in GitHub Actions. Neither hardware compatibility, radio captures, TLS connection to an actual broker, timing of all screen elements nor OTA recovery have been verified on the physical device. A successful build is not a hardware test. No existing Home Assistant production addon was modified.
