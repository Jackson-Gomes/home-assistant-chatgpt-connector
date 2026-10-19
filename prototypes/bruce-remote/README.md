# Bruce remote connector — staging branch

> **Status: prototype only. No remote firmware has been installed.**
> The Home Assistant connector's default branch is unchanged.

## Goal

Create two independent future repositories:

- `bruce-custom-firmware`: fork of https://github.com/BruceDevices/firmware; add an opt-in MQTT/TLS remote client for LilyGO T-Embed CC1101.
- `bruce-chatgpt-connector`: independent connector service, reusing patterns (not HA-specific code) from https://github.com/Jackson-Gomes/home-assistant-chatgpt-connector.

This is temporary staging **on a separate branch** because the linked GitHub integration cannot create repositories. Transfer these files to the corresponding repositories after they are created.

## Work in this branch

- `.github/workflows/bruce-t-embed-build.yml` builds a clean, unmodified **upstream Bruce** for the verified PlatformIO environment `lilygo-t-embed-cc1101`.
- `prototypes/bruce-remote/PROTOCOL.md` proposes an authenticated MQTT command protocol.
- `prototypes/bruce-remote/RESEARCH.md` identifies reusable official sources and constraints.

## Safety and rollout

No broker credentials, Wi-Fi passwords, device tokens or personal identifiers belong in Git.
No remote arbitrary CLI, BadUSB, Wi-Fi attacks, deauth, replay, mass scan or arbitrary JavaScript execution will be exposed.
Start with read-only telemetry and explicit allowlisted benign IR commands on owned devices.
The MQTT control plane will require TLS verification, device authentication, correlation IDs, expiration, non-retained commands, and per-command authorization.
Broker must be reachable from both the ESP32 and the HA host even when the two are on different Wi-Fi networks.

**The baseline .bin in this branch is unmodified Bruce, not the MQTT-enabled custom firmware.**
