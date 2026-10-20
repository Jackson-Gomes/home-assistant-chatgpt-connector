#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>

// Sends a bounded snapshot of the same TFT drawing-command log as Bruce WebUI.
// Not a raw framebuffer (images drawn from local files are references only).
void bruceScreenEnable();
bool bruceScreenBegin(const String &requestId, JsonDocument &metadata);
void bruceScreenPump(PubSubClient &client, const String &root);
bool bruceScreenBusy();
