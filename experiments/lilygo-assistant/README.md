# T-Embed CC1101 Plus voice assistant (prototype)

This is an ESPHome **replacement firmware experiment**, not an update to the existing Bruce build. Do not flash it if you need to retain Bruce's RF, NFC, IR, UI, MQTT connector, or other existing features.

## Status
- [x] Checked board's microphone/speaker GPIOs against LILYGO documentation.
- [x] Checked Home Assistant voice pipelines: **Assist Local** has Portuguese faster-Whisper STT and Piper TTS.
- [x] Wrote ESPHome prototype with PDM mic, I2S speaker, wake word, encoder push-to-talk.
- [ ] ESPHome schema validation/build on target version.
- [ ] Tested microphone recording and speaker on the physical board.
- [ ] Flashed to T-Embed (NOT performed).
- [ ] Verified Home Assistant conversation and hardware stability.

## Pinout

| Component | GPIO |
|---|---|
| PDM microphone clock | 39 |
| PDM microphone data | 42 |
| I2S amplifier BCLK | 46 |
| I2S amplifier LRCLK | 40 |
| I2S audio data | 7 |
| Encoder push | 0 |

Source: https://github.com/Xinyuan-LilyGO/T-Embed-CC1101/blob/master/docs/pinmap_cn.md

## Try after explicit approval to replace Bruce

1. Back up your existing Bruce firmware and working configuration; have the original known-good .bin for recovery.
2. Install ESPHome Device Builder and create a local secrets.yaml, using secrets.example.yaml as a template.
3. Import lilygo-assistant.yaml and run ESPHome Validate/Compile. Inspect log for hardware/PSRAM/audio pin issues.
4. Flash using USB for the first installation, not through Bruce OTA.
5. Adopt the detected ESPHome device in Home Assistant.
6. In the ESPHome voice-assistant settings, choose your **Assist Local** pipeline (Whisper + Piper).
7. Say **Okay Nabu, acenda a luz do escritório** or press encoder to speak.

## Limitations

- The 'Chefe' wake phrase needs a separately trained micro_wake_word model; 'Okay Nabu' is the supported initial model.
- This config does not reproduce the Bruce UI or radio features.
- Microphone and amplifier are on separate I2S buses; confirm actual operation before enabling additional graphics/features.
- Audio, Wi-Fi, BLE and RF together may exceed ESP32-S3 real-time resources.
- This prototype has NOT been compiled or physically validated.

## Preserve Bruce instead?

Integrate a PDM capture task (GPIO39/42) into a **fork of your exact installed Bruce version**, stream frames through the existing secure transport (or a dedicated authenticated endpoint) to HA, then implement HA Assist audio pipeline + TTS reply. That route is firmware source work, not an ESPHome flash. Your Bruce source repository is not currently available through the connected GitHub installation, so this experiment does not claim to change Bruce.
