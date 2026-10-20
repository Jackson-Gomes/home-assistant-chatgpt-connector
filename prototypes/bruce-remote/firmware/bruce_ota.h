#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// Must be internal RAM: flash operations temporarily disable the PSRAM cache.
// Sized for the HTTPS streaming OTA worker, verified at runtime on hardware.
constexpr uint32_t BRUCE_OTA_TASK_STACK_BYTES = 8192;

// Remote OTA requires explicit device-local opt-in; it is not automatically enabled.
// HTTPS CA verification, pinned SHA-256 and dual OTA partitions are mandatory.
bool bruceOtaConfigure(const String &url, const String &trustedRootPem);
bool bruceOtaBegin(const String &id, const String &expectedSha256, String &reason);
bool bruceOtaBusy();
bool bruceOtaPoll(JsonDocument &message); // progress or final; id correlates to request
bool bruceOtaRestartReady();
