#include "bruce_screen.h"
#include <globals.h>
#include <mbedtls/base64.h>

namespace {
constexpr size_t CAPACITY = MAX_LOG_ENTRIES * MAX_LOG_SIZE + 16;
constexpr size_t CHUNK_BYTES = 420; // base64 fits MQTT 2048-byte buffer
uint8_t *buffer = nullptr;
size_t bytes = 0, offset = 0;
String activeId;
uint32_t checksum = 0;
unsigned errors = 0;

uint32_t crc32(const uint8_t *data, size_t n) {
    uint32_t c = 0xFFFFFFFF;
    for (size_t i = 0; i < n; ++i) {
        c ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            c = (c >> 1) ^ (0xEDB88320u & static_cast<uint32_t>(-static_cast<int32_t>(c & 1)));
    }
    return ~c;
}
void clear() {
    if (buffer) free(buffer);
    buffer = nullptr;
    bytes = offset = 0;
    activeId = "";
    errors = 0;
}
}

void bruceScreenEnable() {
    if (!tft.getLogging()) tft.setLogging(true);
}
bool bruceScreenBusy() { return buffer != nullptr; }

bool bruceScreenBegin(const String &requestId, JsonDocument &meta) {
    if (buffer) return false;
    buffer = static_cast<uint8_t *>(psramFound() ? ps_malloc(CAPACITY) : malloc(CAPACITY));
    if (!buffer) return false;
    size_t size = 0;
    // Same binary log format consumed by the official Bruce WebUI renderer.
    tft.getBinLog(buffer, size);
    if (size < 8 || size > CAPACITY) {
        clear();
        return false;
    }
    bytes = size;
    offset = 0;
    activeId = requestId;
    checksum = crc32(buffer, bytes);
    meta["format"] = "bruce-tft-drawlog-v1";
    meta["bytes"] = bytes;
    meta["chunks"] = (bytes + CHUNK_BYTES - 1) / CHUNK_BYTES;
    meta["crc32"] = checksum;
    meta["limitations"] = "UI drawing commands only; local image file pixels are not included";
    return true;
}

void bruceScreenPump(PubSubClient &client, const String &root) {
    if (!buffer || !client.connected()) return;
    size_t index = offset / CHUNK_BYTES;
    size_t length = bytes - offset;
    if (length > CHUNK_BYTES) length = CHUNK_BYTES;
    uint8_t encoded[((CHUNK_BYTES + 2) / 3) * 4 + 8] = {};
    size_t encodedLen = 0;
    if (mbedtls_base64_encode(encoded, sizeof(encoded)-1, &encodedLen, buffer+offset, length) != 0) {
        clear();
        return;
    }
    encoded[encodedLen] = '\0';
    JsonDocument msg;
    msg["v"] = 1;
    msg["id"] = activeId;
    msg["format"] = "bruce-tft-drawlog-v1";
    msg["part"] = index;
    msg["total"] = (bytes + CHUNK_BYTES - 1) / CHUNK_BYTES;
    msg["bytes"] = bytes;
    msg["crc32"] = checksum;
    msg["data"] = reinterpret_cast<const char *>(encoded);
    String payload;
    serializeJson(msg, payload);
    if (!client.publish((root + "/screen").c_str(), payload.c_str())) {
        if (++errors > 5) clear();
        return;
    }
    errors = 0;
    offset += length;
    if (offset >= bytes) clear();
}
