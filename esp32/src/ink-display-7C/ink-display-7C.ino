#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <SPI.h>
#include <time.h>
#include "esp_heap_caps.h"
#include "esp_system.h"

#include <GxEPD2_7C.h>

// =======================
//  调试开关（需要串口时改成 1）
// =======================
#define DEBUG_LOG 0

#if DEBUG_LOG
  #define DBG_BEGIN()    Serial.begin(115200)
  #define DBG_PRINT(x)   Serial.print(x)
  #define DBG_PRINTLN(x) Serial.println(x)
#else
  #define DBG_BEGIN()
  #define DBG_PRINT(x)
  #define DBG_PRINTLN(x)
#endif

// =======================
//  LED 兜底：关掉板载灯
// =======================
#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

// =======================
//  墨水屏参数 & 引脚
// =======================
static const int EPD_WIDTH  = 800;
static const int EPD_HEIGHT = 480;

// 你现在的接线：4, 5, 6, 7, 15, 16
#define PIN_EPD_BUSY 4
#define PIN_EPD_RST  5
#define PIN_EPD_DC   6
#define PIN_EPD_CS   7
#define PIN_EPD_SCLK 15
#define PIN_EPD_DIN  16

// GDEY073D46 / EL073TS3（7.3" 7C）
GxEPD2_7C<
  GxEPD2_730c_GDEY073D46,
  GxEPD2_730c_GDEY073D46::HEIGHT / 4
> display(
  GxEPD2_730c_GDEY073D46(
    PIN_EPD_CS,
    PIN_EPD_DC,
    PIN_EPD_RST,
    PIN_EPD_BUSY
  )
);

// =======================
//  配置存储 / WiFi / WebServer
// =======================
Preferences prefs;
WebServer server(80);

struct Config {
  String  wifi_ssid;
  String  wifi_pass;
  String  backend_hostport;  // m.daihongtao.com:8765
  int32_t tz_offset_hours;   // 时区偏移（整数小时），默认 8
  uint8_t refresh_hour;      // 每天的整点小时 0-23
  bool    rotate180;         // 是否旋转 180°
  bool    valid;
};

// 默认：东八区，每天 8 点刷一次
const char*  DEFAULT_HOSTPORT = "m.daihongtao.com:8765";
const int32_t DEFAULT_TZ      = 8;
const uint8_t DEFAULT_HOUR    = 8;

Config g_cfg;

// =======================
//  配置读写
// =======================
void loadConfig(Config &cfg) {
  prefs.begin("dashcfg", true); // read-only
  cfg.wifi_ssid        = prefs.getString("ssid", "");
  cfg.wifi_pass        = prefs.getString("pass", "");
  cfg.backend_hostport = prefs.getString("hostport", DEFAULT_HOSTPORT);
  cfg.tz_offset_hours  = prefs.getInt("tz", DEFAULT_TZ);
  cfg.refresh_hour     = (uint8_t)prefs.getUChar("hour", DEFAULT_HOUR);
  cfg.rotate180        = prefs.getBool("rot180", false);
  prefs.end();

  cfg.valid = (cfg.wifi_ssid.length() > 0);

#if DEBUG_LOG
  DBG_PRINT("[CFG] ssid="); DBG_PRINTLN(cfg.wifi_ssid);
  DBG_PRINT("[CFG] hostport="); DBG_PRINTLN(cfg.backend_hostport);
  DBG_PRINT("[CFG] tz_offset_hours="); DBG_PRINTLN(cfg.tz_offset_hours);
  DBG_PRINT("[CFG] refresh_hour="); DBG_PRINTLN((int)cfg.refresh_hour);
  DBG_PRINT("[CFG] rotate180="); DBG_PRINTLN(cfg.rotate180 ? "true" : "false");
  DBG_PRINT("[CFG] valid="); DBG_PRINTLN(cfg.valid ? "true" : "false");
#endif
}

void saveConfig(const Config &cfg) {
  prefs.begin("dashcfg", false);
  prefs.putString("ssid", cfg.wifi_ssid);
  prefs.putString("pass", cfg.wifi_pass);
  prefs.putString("hostport", cfg.backend_hostport);
  prefs.putInt("tz", cfg.tz_offset_hours);
  prefs.putUChar("hour", cfg.refresh_hour);
  prefs.putBool("rot180", cfg.rotate180);
  prefs.end();
}

// =======================
//  HTML 工具 & 配置页
// =======================
String htmlEscape(const String &s) {
  String out;
  out.reserve(s.length());
  for (size_t i = 0; i < s.length(); ++i) {
    char c = s[i];
    if      (c == '&')  out += F("&amp;");
    else if (c == '<')  out += F("&lt;");
    else if (c == '>')  out += F("&gt;");
    else if (c == '"')  out += F("&quot;");
    else                out += c;
  }
  return out;
}

String buildConfigPage() {
  int n = WiFi.scanNetworks();

  String curSsid = g_cfg.wifi_ssid;
  String host    = htmlEscape(g_cfg.backend_hostport);
  int32_t tz     = g_cfg.tz_offset_hours;
  if (tz < -12 || tz > 14) tz = DEFAULT_TZ;
  uint8_t hour   = g_cfg.refresh_hour;
  if (hour > 23) hour = DEFAULT_HOUR;
  bool rot180    = g_cfg.rotate180;

  String html = F(
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Bili-Insight墨水屏设置</title></head><body>"
    "<h2>Bili-Insight墨水屏设置</h2>"
    "<form method='POST' action='/save'>"
    "WiFi SSID:<br><select name='ssid'>"
  );

  if (n <= 0) {
    html += F("<option value=''>未扫描到WiFi</option>");
  } else {
    for (int i = 0; i < n; ++i) {
      String s   = WiFi.SSID(i);
      String esc = htmlEscape(s);
      html += "<option value='";
      html += esc;
      html += "'";
      if (s == curSsid) html += " selected";
      html += ">";
      html += esc;
      html += "</option>";
    }
  }
  html += F("</select><br><br>");

  html += F("密码:<br><input name='pass' type='password'><br><br>");

  html += F("服务器:<br><input name='hostport' size='40' value='");
  html += host;
  html += F("'><br><br>");

  html += F("每日刷新时间（0-23 点整）：<br><select name='hour'>");
  for (int h = 0; h < 24; ++h) {
    html += "<option value='";
    html += String(h);
    html += "'";
    if (h == hour) html += " selected";
    html += ">";
    html += String(h);
    html += F(" 点</option>");
  }
  html += F("</select><br><small>设备每天会在该小时左右刷新一次</small><br><br>");

  html += F("时区偏移:<br><select name='tz'>");
  for (int t = -12; t <= 14; ++t) {
    html += "<option value='";
    html += String(t);
    html += "'";
    if (t == tz) html += " selected";
    html += ">";
    if (t >= 0) html += "+";
    html += String(t);
    html += F("</option>");
  }
  html += F("</select><br><small>默认 +8（东八区）</small><br><br>");

  html += F("<label><input type='checkbox' name='rot180' value='1'");
  if (rot180) html += F(" checked");
  html += F("> 画面旋转 180°</label><br><br>");

  html += F("<input type='submit' value='保存并重启'>"
            "</form></body></html>");

  return html;
}

// =======================
//  WebServer 处理
// =======================
void handleRoot() {
  server.send(200, "text/html; charset=utf-8", buildConfigPage());
}

void handleSave() {
  String ssid     = server.arg("ssid");
  String pass     = server.arg("pass");
  String host     = server.arg("hostport");
  String hourStr  = server.arg("hour");
  String tzStr    = server.arg("tz");
  bool rot180Req  = (server.arg("rot180") == "1");

  Config newCfg = g_cfg;

  if (ssid.length() > 0) newCfg.wifi_ssid = ssid;
  if (pass.length() > 0) newCfg.wifi_pass = pass;
  if (host.length() > 0) newCfg.backend_hostport = host;

  int32_t tz = tzStr.toInt();
  if (tz < -12) tz = -12;
  if (tz > 14)  tz = 14;
  newCfg.tz_offset_hours = tz;

  int hour = hourStr.toInt();
  if (hour < 0)  hour = 0;
  if (hour > 23) hour = 23;
  newCfg.refresh_hour = (uint8_t)hour;

  newCfg.rotate180 = rot180Req;
  newCfg.valid     = (newCfg.wifi_ssid.length() > 0);

  saveConfig(newCfg);

  server.send(
    200,
    "text/html; charset=utf-8",
    F("<html><body><h3>保存成功，设备即将重启...</h3></body></html>")
  );

  delay(1000);
  ESP.restart();
}

void startConfigPortal() {
  WiFi.mode(WIFI_AP_STA);

  String apSsid     = "BiliDashboard-" + String((uint32_t)ESP.getEfuseMac(), HEX).substring(4);
  const char* apPwd = "12345678";

  WiFi.softAP(apSsid.c_str(), apPwd);

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();

  // 配置模式：不休眠，一直跑 WebServer
  for (;;) {
    server.handleClient();
    delay(10);
  }
}

// =======================
//  WiFi & 时间
// =======================
bool connectWiFi(const Config &cfg, uint32_t timeout_ms = 15000) {
  if (cfg.wifi_ssid.isEmpty()) return false;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);                           // STA 省电
  WiFi.setTxPower(WIFI_POWER_8_5dBm);            // 降功率，足够连路由器就行
  WiFi.begin(cfg.wifi_ssid.c_str(), cfg.wifi_pass.c_str());

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeout_ms) {
    delay(200);
  }
  return WiFi.status() == WL_CONNECTED;
}

bool syncTime(const Config &cfg, struct tm &outLocal) {
  long offsetSec = (long)cfg.tz_offset_hours * 3600;
  configTime(offsetSec, 0, "pool.ntp.org", "time.nist.gov", "ntp.aliyun.com");

  for (int i = 0; i < 30; ++i) { // 最多等 15 秒
    if (getLocalTime(&outLocal)) {
#if DEBUG_LOG
      char buf[64];
      strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &outLocal);
      DBG_PRINT("[TIME] OK: "); DBG_PRINTLN(buf);
#endif
      return true;
    }
    delay(500);
  }
  return false;
}

// =======================
//  HTTP 下载 dashboard.bin 到 PSRAM
// =======================
uint8_t* framebuffer = nullptr;

bool downloadDashboardBin(const Config &cfg) {
  size_t target = (size_t)EPD_WIDTH * EPD_HEIGHT; // 384000 bytes

  if (!framebuffer) {
    // 优先用 PSRAM
    framebuffer = (uint8_t*)heap_caps_malloc(
      target,
      MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM
    );
    if (!framebuffer) {
      // 兜底用内部 RAM（理论上不太够，但保留）
      framebuffer = (uint8_t*)heap_caps_malloc(target, MALLOC_CAP_8BIT);
    }
  }
  if (!framebuffer) return false;

  // 构造 URL: http://host:port/api/esp32/dashboard.bin
  String url;
  String hp = cfg.backend_hostport;
  hp.trim();
  if (hp.startsWith("http://") || hp.startsWith("https://")) {
    url = hp;
  } else {
    url = "http://" + hp + "/api/esp32/dashboard.bin";
  }

  HTTPClient http;
  http.begin(url);
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    http.end();
    return false;
  }

  int len = http.getSize();
  WiFiClient *stream = http.getStreamPtr();
  size_t total = 0;

  while (http.connected() && (len > 0 || len == -1) && total < target) {
    size_t avail = stream->available();
    if (avail) {
      size_t toRead = avail;
      if (toRead > target - total) toRead = target - total;
      int r = stream->read(framebuffer + total, toRead);
      if (r > 0) {
        total += r;
        if (len > 0) len -= r;
      }
    } else {
      delay(1);
    }
  }

  http.end();

  if (total != target) {
#if DEBUG_LOG
    DBG_PRINT("[HTTP] size mismatch, expect=");
    DBG_PRINT((int)target);
    DBG_PRINT(" got=");
    DBG_PRINTLN((int)total);
#endif
    return false;
  }

  return true;
}

// =======================
//  墨水屏显示
// =======================
void initDisplay(const Config &cfg) {
  SPI.end();
  SPI.begin(PIN_EPD_SCLK, -1 /*MISO*/, PIN_EPD_DIN, PIN_EPD_CS);

  // 0 = 无串口输出
  display.init(0, true, 2, false);
  if (cfg.rotate180) {
    display.setRotation(3);
  } else {
    display.setRotation(1);
  }
}

void drawFromFramebuffer() {
  display.setFullWindow();
  display.epd2.drawDemoBitmap(
    framebuffer,
    0, 0, 0,
    EPD_WIDTH, EPD_HEIGHT,
    0,
    false,
    false   // 数据在 RAM
  );
  display.hibernate();
}

// =======================
//  Deep Sleep 工具
// =======================
void prepareDeepSleepDomains() {
  // 关掉 RTC 外设（如果没有 RTC IO 唤醒）
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_OFF);
  // RTC SLOW / FAST 内存都不用
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_SLOW_MEM, ESP_PD_OPTION_OFF);
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_FAST_MEM, ESP_PD_OPTION_OFF);
}

void goDeepSleepMinutes(uint32_t minutes) {
  if (minutes < 10)   minutes = 10;
  if (minutes > 1440) minutes = 1440; // 最多 24 小时

#if DEBUG_LOG
  DBG_PRINT("[SLEEP] minutes="); DBG_PRINTLN((int)minutes);
#endif

  uint64_t us = (uint64_t)minutes * 60ULL * 1000000ULL;

  // 关 WiFi / BT
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
#if defined(BLUEFRUIT_FEATHER) || defined(CONFIG_BT_ENABLED)
  btStop();
#endif
  prepareDeepSleepDomains();
  esp_sleep_enable_timer_wakeup(us);
  esp_deep_sleep_start();
}

// 计算距离下一个“cfg.refresh_hour:00”的分钟数
void sleepUntilNextSchedule(const Config &cfg, bool hasTime, const struct tm &now) {
  if (!hasTime) {
    goDeepSleepMinutes(1440);
    return;
  }

  int curMinOfDay = now.tm_hour * 60 + now.tm_min;
  int targetMin   = (int)cfg.refresh_hour * 60;
  int delta;

  if (curMinOfDay < targetMin) {
    delta = targetMin - curMinOfDay;
  } else {
    delta = 24 * 60 - (curMinOfDay - targetMin);
  }

  if (delta < 1) delta = 24 * 60;

  goDeepSleepMinutes((uint32_t)delta);
}

// =======================
//  setup / loop
// =======================
void setup() {
  setCpuFrequencyMhz(80);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);  // 熄灯

  DBG_BEGIN();
#if DEBUG_LOG
  DBG_PRINTLN("===== ESP32-S3 Bili-Insight Ink Display boot (release) =====");
#endif

  loadConfig(g_cfg);

  if (!g_cfg.valid) {
    // 首次启动 / 无配置 → AP 配置模式
    startConfigPortal(); // 不返回
  }

  // 有配置 → 连 WiFi
  if (!connectWiFi(g_cfg)) {
    // 连不上就进入 AP 配置模式（方便改 WiFi）
    startConfigPortal(); // 不返回
  }

  // NTP 同步时间（用于下一次唤醒时间计算）
  struct tm timeinfo;
  bool hasTime = syncTime(g_cfg, timeinfo);

  // 上电必刷一次
  bool ok = downloadDashboardBin(g_cfg);
  if (ok) {
    initDisplay(g_cfg);
    drawFromFramebuffer();
  }

  // 刷完后直接算下次唤醒时间并 deep sleep
  if (!hasTime) {
    struct tm tmp;
    if (syncTime(g_cfg, tmp)) {
      sleepUntilNextSchedule(g_cfg, true, tmp);
    } else {
      sleepUntilNextSchedule(g_cfg, false, timeinfo);
    }
  } else {
    sleepUntilNextSchedule(g_cfg, true, timeinfo);
  }
}

void loop() {
  // 不使用
}