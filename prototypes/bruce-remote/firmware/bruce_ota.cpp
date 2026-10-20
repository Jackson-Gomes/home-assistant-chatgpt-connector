#include "bruce_ota.h"
#include <HTTPClient.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>
#include <mbedtls/version.h>
#include <atomic>

namespace {
String url, rootPem, expectedHex, reqId, finishedReason;
std::atomic<bool> running(false), done(false), success(false);
std::atomic<uint32_t> progress(0);
uint32_t lastProgress = 0, restartAt = 0;
bool progressSent = false;
bool configured = false;

bool shaHex(const String &s) {
    if (s.length() != 64) return false;
    for (size_t i=0; i<s.length(); ++i) {
        char c=s.charAt(i);
        if (!((c>='0' && c<='9') || (c>='a' && c<='f'))) return false;
    }
    return true;
}
void finish(bool ok, const char *why) {
    finishedReason=why;
    success.store(ok, std::memory_order_relaxed);
    done.store(true, std::memory_order_release);
}
void updateWorker(void *) {
    const esp_partition_t *slot=esp_ota_get_next_update_partition(nullptr);
    if (!slot) {
        finish(false,"no_second_ota_partition"); vTaskDelete(nullptr); return;
    }
    if (WiFi.status()!=WL_CONNECTED) {
        finish(false,"wifi_offline"); vTaskDelete(nullptr); return;
    }
    WiFiClientSecure tls;
    tls.setCACert(rootPem.c_str());
    tls.setTimeout(12);
    HTTPClient http;
    http.setTimeout(12000);
    http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
    if (!http.begin(tls,url)) {
        finish(false,"invalid_https_url"); vTaskDelete(nullptr); return;
    }
    int code=http.GET();
    const int n=http.getSize();
    if (code!=HTTP_CODE_OK || n<=0 || static_cast<uint32_t>(n)>slot->size) {
        http.end();
        finish(false,"http_error_or_image_does_not_fit");
        vTaskDelete(nullptr); return;
    }
    if (!Update.begin(n,U_FLASH)) {
        http.end();
        finish(false,"ota_partition_begin_failed");
        vTaskDelete(nullptr); return;
    }
    mbedtls_sha256_context hash;
    mbedtls_sha256_init(&hash);
#if MBEDTLS_VERSION_MAJOR >= 3
    mbedtls_sha256_starts(&hash,0);
#else
    mbedtls_sha256_starts_ret(&hash,0);
#endif
    WiFiClient *stream=http.getStreamPtr();
    uint8_t chunk[1024];
    int remaining=n;
    uint32_t lastRx=millis();
    bool transferOk=true;
    while (remaining>0) {
        if (WiFi.status()!=WL_CONNECTED || static_cast<uint32_t>(millis()-lastRx)>20000UL) {
            transferOk=false; break;
        }
        int available=stream->available();
        if (available<=0) {
            vTaskDelay(pdMS_TO_TICKS(15));
            continue;
        }
        size_t amount=static_cast<size_t>(available);
        if (amount>sizeof(chunk)) amount=sizeof(chunk);
        if (amount>static_cast<size_t>(remaining)) amount=remaining;
        int got=stream->readBytes(chunk,amount);
        if (got<=0) {
            transferOk=false; break;
        }
        lastRx=millis();
#if MBEDTLS_VERSION_MAJOR >= 3
        mbedtls_sha256_update(&hash,chunk,got);
#else
        mbedtls_sha256_update_ret(&hash,chunk,got);
#endif
        if (Update.write(chunk,got)!=static_cast<size_t>(got)) {
            transferOk=false; break;
        }
        remaining-=got;
        progress.store(static_cast<uint32_t>((static_cast<uint64_t>(n-remaining)*100)/n),
                       std::memory_order_relaxed);
        vTaskDelay(1);
    }
    uint8_t digest[32]={};
#if MBEDTLS_VERSION_MAJOR >= 3
    mbedtls_sha256_finish(&hash,digest);
#else
    mbedtls_sha256_finish_ret(&hash,digest);
#endif
    mbedtls_sha256_free(&hash);
    http.end();
    if (!transferOk || remaining!=0) {
        Update.abort();
        finish(false,"ota_download_or_write_failed");
        vTaskDelete(nullptr); return;
    }
    char computed[65];
    static const char hd[]="0123456789abcdef";
    for (int i=0;i<32;++i) {
        computed[2*i]=hd[digest[i]>>4];
        computed[2*i+1]=hd[digest[i]&15];
    }
    computed[64]='\0';
    if (expectedHex!=String(computed)) {
        Update.abort(); // leave active partition untouched
        finish(false,"sha256_mismatch");
        vTaskDelete(nullptr); return;
    }
    if (!Update.end()) {
        Update.abort();
        finish(false,"esp_ota_finalize_failed");
        vTaskDelete(nullptr); return;
    }
    finish(true,"verified_and_ready_to_reboot");
    vTaskDelete(nullptr);
}
} // namespace

bool bruceOtaConfigure(const String &updateUrl, const String &caPem) {
    configured=false;
    if (!updateUrl.startsWith("https://") || updateUrl.length()>300 ||
        updateUrl.indexOf('@')>=0 || updateUrl.indexOf('#')>=0 ||
        updateUrl.indexOf('?')>=0 || caPem.indexOf("-----BEGIN CERTIFICATE-----")<0)
        return false;
    url=updateUrl;
    rootPem=caPem;
    configured=true;
    return true;
}
bool bruceOtaBusy() { return running.load() && !done.load(); }
bool bruceOtaBegin(const String &id, const String &expectedSha256, String &reason) {
    if (!configured) { reason="ota_not_locally_configured"; return false; }
    if (running.load()) { reason="ota_already_started"; return false; }
    if (!shaHex(expectedSha256)) { reason="sha256_must_be_lowercase_hex"; return false; }
    if (!esp_ota_get_next_update_partition(nullptr)) {
        reason="dual_ota_partitions_required"; return false;
    }
    reqId=id;
    expectedHex=expectedSha256;
    lastProgress=0;
    progressSent=false;
    progress.store(0);
    done.store(false);
    success.store(false);
    running.store(true);
    if (xTaskCreate(updateWorker,"BruceOTA",16384,nullptr,1,nullptr)!=pdPASS) {
        running.store(false);
        reason="ota_task_allocation_failed"; return false;
    }
    return true;
}
bool bruceOtaPoll(JsonDocument &msg) {
    if (!running.load()) return false;
    bool isDone=done.load(std::memory_order_acquire);
    uint32_t percent=progress.load();
    if (!isDone && (percent==lastProgress || percent<lastProgress+5) && progressSent) return false;
    progressSent=true;
    lastProgress=percent;
    msg["v"]=1;
    msg["id"]=reqId;
    msg["percent"]=percent;
    msg["done"]=isDone;
    if (isDone) {
        msg["ok"]=success.load();
        msg["reason"]=finishedReason;
        if (success.load()) restartAt=millis()+5000;
        running.store(false);
    }
    return true;
}
bool bruceOtaRestartReady() {
    return restartAt && static_cast<int32_t>(millis()-restartAt)>=0;
}
