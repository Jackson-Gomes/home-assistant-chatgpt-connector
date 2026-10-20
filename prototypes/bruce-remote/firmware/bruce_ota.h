#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// Remote-initiated OTA is disabled unless configured locally and physically confirmed.
// HTTPS CA verification, pinned SHA-256 and dual OTA partitions are mandatory.
bool bruceOtaConfigure(const String &url, const String &trustedRootPem);
bool bruceOtaBegin(const String &id, const String &expectedSha256, String &reason);
bool bruceOtaBusy();
bool bruceOtaPoll(JsonDocument &message); // progress or final; id correlates to request
bool bruceOtaRestartReady();
