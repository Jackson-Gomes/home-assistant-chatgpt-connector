# Prior art / sources (researched 2026-10-19)

1. Official Bruce serial commands and HTTP `POST /cm`:
   https://wiki.bruce.computer/controlling-device/serial/
   Existing command parser can be reused **locally** via a safe dispatcher; sending unauthenticated HTTP over public internet is inappropriate.
2. Bruce WebUI:
   https://wiki.bruce.computer/controlling-device/webui/
   WebUI runs in local network or AP; it is not a remote cross-network service. Default web credentials should be changed.
3. Bruce build pipeline:
   https://github.com/BruceDevices/firmware/blob/main/.github/workflows/buil_parallel.yml
   PlatformIO target `lilygo-t-embed-cc1101` already exists; upstream build.py merges bootloader, partition table and firmware.
4. ESP32 MQTT / Home Assistant bridge example:
   https://github.com/tomsepe/esp32-433MHz-remote-zigbee
   Demonstrates ESP32 + MQTT command channels + HA discovery. Use concepts, not its hardcoded radio commands.
5. Bruce custom T-Embed fork:
   https://github.com/Epickitrolaz/BruceFirmware
   Shows community board-specific custom builds.
6. Bruce auto-start issue:
   https://github.com/BruceDevices/firmware/issues/1549
   WebUI startup may need a compatibility check on the selected baseline.

Finding: no verified drop-in project for exactly *Bruce + T-Embed CC1101 + secure roaming MQTT + ChatGPT connector*. Implement incrementally and verify on hardware.
