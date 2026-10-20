#include "bruce_remote.h"
#include "bruce_screen.h"
#include "bruce_ota.h"
#include "modules/ir/ir_read.h"
#include "modules/rf/rf_scan.h"
#include <esp_ota_ops.h>
#include "core/serialcmds.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <globals.h>
#include <time.h>

namespace {
struct Settings {
    String id, host, username, password, caPath;
    bool otaReady = false;
    uint16_t port = 8883;
    JsonDocument actions;
} cfg;
WiFiClientSecure tls;
PubSubClient mqtt(tls);
String caPem, rootTopic;
String ids[12];
uint8_t idPointer = 0;
uint32_t nextConnect = 0, lastHeartbeat = 0, unlockedUntil = 0, lastCommand = 0;
constexpr uint32_t ARM_DURATION = 300000UL;
constexpr uint32_t HEARTBEAT_INTERVAL = 45000UL;
const char * const operations[] = {
    "info.get", "remote.capabilities", "remote.unlock", "remote.lock",
    "screen.brightness", "screen.color", "sound.tone", "sound.say",
    "menu.nav", "menu.option", "ir.send_saved", "rf.send_saved",
    "ir.receive", "rf.receive", "rf.scan", "screen.snapshot",
    "ota.status", "ota.apply"
};
bool word(const String &s, size_t max) {
    if (s.isEmpty() || s.length() > max) return false;
    for (size_t i=0; i<s.length(); ++i) {
        char c=s.charAt(i);
        if (!(isalnum(static_cast<unsigned char>(c)) || c=='_' || c=='-')) return false;
    }
    return true;
}
bool hex(const String &s, unsigned len) {
    if (s.length()!=len) return false;
    for (unsigned i=0;i<len;++i) if (!isxdigit(static_cast<unsigned char>(s[i]))) return false;
    return true;
}
bool armed() { return unlockedUntil && static_cast<int32_t>(unlockedUntil-millis())>0; }

bool readConfig() {
    File file=LittleFS.open("/bruce-remote.json","r");
    if (!file || file.size()>6144) return false;
    JsonDocument doc;
    auto error=deserializeJson(doc,file);
    file.close();
    if (error || !doc["enabled"].as<bool>()) return false;
    cfg.id=doc["device_id"].as<String>();
    cfg.host=doc["broker"].as<String>();
    cfg.username=doc["username"].as<String>();
    cfg.password=doc["password"].as<String>();
    cfg.port=doc["port"]|8883;
    cfg.caPath=doc["ca_path"]|"/bruce-ca.pem";
    if (!word(cfg.id,32) || cfg.host.isEmpty() || cfg.host.length()>180 ||
        cfg.host.indexOf('/')>=0 || cfg.port==0 ||
        cfg.username.isEmpty() || cfg.password.isEmpty() ||
        !cfg.caPath.startsWith("/") || cfg.caPath.indexOf("..")>=0) return false;
    File ca=LittleFS.open(cfg.caPath,"r");
    if (!ca || ca.size()<100 || ca.size()>8192) return false;
    caPem=ca.readString();
    ca.close();
    if (caPem.indexOf("-----BEGIN CERTIFICATE-----")<0) return false;
    cfg.actions.set(doc["actions"]);
    JsonVariantConst update = doc["ota"];
    if (update["enabled"] | false) {
        const String certFile = update["ca_path"] | "/bruce-ota-ca.pem";
        if (certFile.startsWith("/") && certFile.indexOf("..")<0) {
            File pem = LittleFS.open(certFile,"r");
            if (pem && pem.size()>=100 && pem.size()<8192) {
                String trust = pem.readString();
                cfg.otaReady = bruceOtaConfigure(update["url"].as<String>(),trust);
            }
            pem.close();
        }
    }
    rootTopic="bruce/v1/"+cfg.id;
    return true;
}
void response(const String &id, bool ok, const char *reason=nullptr, JsonDocument *result=nullptr) {
    JsonDocument doc;
    doc["v"]=1;
    doc["id"]=id;
    doc["ok"]=ok;
    if (reason) doc["reason"]=reason;
    if (result) doc["result"]=result->as<JsonVariantConst>();
    String value;
    serializeJson(doc,value);
    mqtt.publish((rootTopic+"/reply").c_str(),value.c_str());
}
void queued(const String &id, const String &cmd) {
    if (cmd.length()>180 || !parseSerialCommand(cmd,false)) {
        response(id,false,"queue_busy_or_command_too_long");
        return;
    }
    JsonDocument data;
    data["queued"]=true;
    response(id,true,"queued_not_executed",&data);
}
void online() {
    mqtt.publish((rootTopic+"/status").c_str(),
                 "{\"online\":true,\"firmware\":\"BruceRemote/0.1\"}",true);
}
void onCommand(char *topic, uint8_t *bytes, unsigned int length) {
    if (String(topic)!=rootTopic+"/cmd" || !length || length>1400) return;
    JsonDocument msg;
    if (deserializeJson(msg,bytes,length)) return;
    String id=msg["id"].as<String>();
    if (!word(id,64) || id.length()<8 || (msg["v"]|0)!=1) return;
    if (millis()-lastCommand<350) { response(id,false,"rate_limited"); return; }
    lastCommand=millis();
    for (const String &seen : ids) if (seen==id) {
        response(id,false,"duplicate_id"); return;
    }
    ids[idPointer]=id;
    idPointer=(idPointer+1)%12;
    const int64_t now=static_cast<int64_t>(time(nullptr));
    const int64_t exp=msg["expires"]|static_cast<int64_t>(0);
    if (now<1760000000 || exp<=now || exp>now+120) {
        response(id,false,"invalid_or_expired_timestamp"); return;
    }
    const String op=msg["op"].as<String>();
    const JsonVariantConst a=msg["args"].as<JsonVariantConst>();
    if (op=="remote.capabilities") {
        JsonDocument r;
        JsonArray ops=r["operations"].to<JsonArray>();
        for (auto name:operations) ops.add(name);
        r["armed"]=armed();
        response(id,true,nullptr,&r);
    } else if (op=="info.get") {
        JsonDocument r;
        r["device_id"]=cfg.id;
        r["board"]="lilygo-t-embed-cc1101";
        r["uptime_ms"]=millis();
        r["free_heap"]=ESP.getFreeHeap();
        r["free_psram"]=ESP.getFreePsram();
        r["wifi_connected"]=WiFi.isConnected();
        r["wifi_rssi"]=WiFi.isConnected()?WiFi.RSSI():0;
        r["brightness"]=bruceConfig.bright;
        r["sound_enabled"]=bruceConfig.soundEnabled;
        r["sd_mounted"]=sdcardMounted;
        r["armed"]=armed();
        response(id,true,nullptr,&r);
    } else if (op=="remote.lock") {
        unlockedUntil=0;
        response(id,true,"locked");
    } else if (op=="remote.unlock") {
        // No network-only elevation: local user MUST be pressing the encoder.
        if (digitalRead(ENCODER_KEY)!=LOW) {
            response(id,false,"hold_encoder_center_to_unlock"); return;
        }
        unlockedUntil=millis()+ARM_DURATION;
        response(id,true,"unlocked_for_5_minutes");
    } else if (op=="ota.status") {
        JsonDocument r;
        r["configured"]=cfg.otaReady;
        r["busy"]=bruceOtaBusy();
        const esp_partition_t *running=esp_ota_get_running_partition();
        const esp_partition_t *next=esp_ota_get_next_update_partition(nullptr);
        r["dual_partition"]=next!=nullptr;
        if (running) r["running_partition"]=running->label;
        if (next) {
            r["next_partition"]=next->label;
            r["max_app_bytes"]=next->size;
        }
        response(id,true,nullptr,&r);
    } else if (op=="ota.apply") {
        if (!armed() || digitalRead(ENCODER_KEY)!=LOW) {
            response(id,false,"physical_unlock_and_button_hold_required");
            return;
        }
        if (!cfg.otaReady) { response(id,false,"ota_not_configured_locally"); return; }
        String reason;
        if (!bruceOtaBegin(id,a["sha256"].as<String>(),reason)) {
            response(id,false,reason.c_str()); return;
        }
        response(id,true,"ota_started_progress_on_ota_topic");
    } else if (op=="screen.snapshot") {
        if (!armed()) { response(id,false,"physical_unlock_required"); return; }
        JsonDocument meta;
        if (!bruceScreenBegin(id,meta)) {
            response(id,false,"screen_busy_or_unavailable"); return;
        }
        response(id,true,"screen_chunk_stream_started",&meta);
    } else if (op=="screen.brightness") {
        int percent=a["percent"]|-1;
        if (percent<1 || percent>100) {
            response(id,false,"invalid_brightness"); return;
        }
        queued(id,"screen brightness "+String(percent*255/100));
    } else if (op=="screen.color") {
        String color=a["hex"].as<String>();
        if (!hex(color,6)) { response(id,false,"invalid_color"); return; }
        queued(id,"screen color hex "+color);
    } else if (op=="sound.tone") {
        int hz=a["hz"]|-1, ms=a["ms"]|-1;
        if (hz<100 || hz>4000 || ms<30 || ms>1500) {
            response(id,false,"tone_out_of_bounds"); return;
        }
        queued(id,"tone "+String(hz)+" "+String(ms));
    } else if (op=="sound.say") {
        String phrase=a["text"].as<String>();
        phrase.trim();
        if (phrase.isEmpty() || phrase.length()>96 ||
            phrase.indexOf('\n')>=0 || phrase.indexOf('\r')>=0 ||
            phrase.indexOf('"')>=0 || phrase.indexOf('\\')>=0) {
            response(id,false,"invalid_text"); return;
        }
        queued(id,"say "+phrase);
    } else if (op=="menu.nav" || op=="menu.option") {
        if (!armed()) { response(id,false,"physical_unlock_required"); return; }
        if (op=="menu.nav") {
            String direction=a["button"].as<String>();
            if (direction!="next" && direction!="prev" &&
                direction!="up" && direction!="down" &&
                direction!="select" && direction!="esc" &&
                direction!="nextpage" && direction!="prevpage") {
                response(id,false,"invalid_direction"); return;
            }
            queued(id,"nav "+direction);
        } else {
            int n=a["index"]|-1;
            if (n<0 || n>100) { response(id,false,"invalid_index"); return; }
            queued(id,"option "+String(n));
        }
    } else if (op=="ir.send_saved" || op=="rf.send_saved") {
        if (!armed()) { response(id,false,"physical_unlock_required"); return; }
        String name=a["name"].as<String>();
        if (!word(name,32)) { response(id,false,"invalid_name"); return; }
        JsonVariantConst saved=cfg.actions[op=="ir.send_saved"?"ir":"rf"][name];
        if (saved.isNull()) { response(id,false,"not_locally_preapproved"); return; }
        if (op=="ir.send_saved") {
            String proto=saved["protocol"].as<String>();
            String address=saved["address"].as<String>();
            String command=saved["command"].as<String>();
            if (!word(proto,20) || !hex(address,8) || !hex(command,8)) {
                response(id,false,"invalid_saved_ir"); return;
            }
            queued(id,"ir tx "+proto+" "+address+" "+command);
        } else {
            String key=saved["key"].as<String>();
            uint32_t f=saved["frequency"]|0UL;
            uint32_t te=saved["te"]|0UL;
            uint32_t count=saved["count"]|0UL;
            if (!hex(key,6) || (f!=315000000UL && f!=433920000UL &&
                f!=868350000UL && f!=915000000UL) ||
                te<100 || te>1000 || count<1 || count>3) {
                response(id,false,"invalid_saved_rf"); return;
            }
            queued(id,"subghz tx "+key+" "+String(f)+" "+
                  String(te)+" "+String(count));
        }
    } else if (op=="ir.receive" || op=="rf.receive" || op=="rf.scan") {
        if (!armed()) { response(id,false,"physical_unlock_required"); return; }
        if (op=="ir.receive") {
            IrRead ir(true,false);
            String result=ir.loop_headless(6);
            bool truncated=result.length()>900;
            if (truncated) result.remove(900);
            JsonDocument r;
            r["data"]=result;
            r["truncated"]=truncated;
            r["received"]=result.length()>0;
            response(id,true,"ir_capture_complete",&r);
        } else {
            uint32_t f=a["frequency"]|0UL;
            if (f!=315000000UL && f!=433920000UL &&
                f!=868350000UL && f!=915000000UL) {
                response(id,false,"invalid_frequency"); return;
            }
            String result;
            if (op=="rf.receive") result=rfReceiveSignal(float(f)/1000000.0f,6,false,true);
            else result=rf_scan(float(f)/1000000.0f,float(f+1000000UL)/1000000.0f,600);
            bool truncated=result.length()>900;
            if (truncated) result.remove(900);
            JsonDocument r;
            r["data"]=result;
            r["truncated"]=truncated;
            r["received"]=result.length()>0;
            response(id,true,"rf_capture_complete",&r);
        }
    } else {
        response(id,false,"unknown_or_disabled_operation");
    }
}
void task(void *) {
    if (!readConfig()) {
        Serial.println("[BruceRemote] Disabled: missing opt-in config or CA");
        vTaskDelete(nullptr);
        return;
    }
    bruceScreenEnable(); 
    tls.setCACert(caPem.c_str()); // Verify MQTT broker certificate and hostname.
    tls.setTimeout(6);
    mqtt.setServer(cfg.host.c_str(),cfg.port);
    mqtt.setBufferSize(2048);
    mqtt.setSocketTimeout(6);
    mqtt.setKeepAlive(25);
    mqtt.setCallback(onCommand);
    bool ntpStarted=false;
    for (;;) {
        if (WiFi.status()!=WL_CONNECTED) {
            if (mqtt.connected()) mqtt.disconnect();
            ntpStarted=false;
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        if (!ntpStarted) {
            configTime(0,0,"pool.ntp.org","time.google.com");
            ntpStarted=true;
        }
        if (!mqtt.connected()) {
            if (static_cast<int32_t>(millis()-nextConnect)<0) {
                vTaskDelay(pdMS_TO_TICKS(200)); continue;
            }
            nextConnect=millis()+6000;
            if (time(nullptr)<1760000000) {
                vTaskDelay(pdMS_TO_TICKS(1000)); continue;
            }
            String willTopic=rootTopic+"/status";
            if (mqtt.connect(cfg.id.c_str(),cfg.username.c_str(),cfg.password.c_str(),
                willTopic.c_str(),1,true,"{\"online\":false}")) {
                mqtt.subscribe((rootTopic+"/cmd").c_str(),1);
                online();
                lastHeartbeat=millis();
                Serial.println("[BruceRemote] Verified TLS MQTT connected");
            }
        } else {
            mqtt.loop();
            bruceScreenPump(mqtt,rootTopic);
            JsonDocument otaNotice;
            if (bruceOtaPoll(otaNotice)) {
                String report;
                serializeJson(otaNotice,report);
                mqtt.publish((rootTopic+"/ota").c_str(),report.c_str());
            }
            if (bruceOtaRestartReady()) {
                mqtt.disconnect();
                vTaskDelay(pdMS_TO_TICKS(150));
                ESP.restart();
            }
            if (millis()-lastHeartbeat>45000UL) {
                lastHeartbeat=millis();
                online();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}
} // namespace
void startBruceRemoteBridge() {
    if (xTaskCreate(task,"BruceRemote",12288,nullptr,1,nullptr)!=pdPASS)
        Serial.println("[BruceRemote] Task allocation failed");
}
