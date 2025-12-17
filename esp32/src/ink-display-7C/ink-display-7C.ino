#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <SPI.h>
#include <time.h>
#include "esp_heap_caps.h"
#include "esp_system.h"

#include <GxEPD2_7C.h>
#include <HardwareSerial.h>
#include "esp_wifi.h"
#include "esp_bt.h"

#include "driver/gpio.h"
#include "driver/rtc_io.h"

// =======================
//  调试开关（需要串口时改成 1）
// =======================
#define DEBUG_LOG 1

HardwareSerial DebugSerial(0);

#if DEBUG_LOG
  #define DBG_BEGIN()    DebugSerial.begin(115200)
  #define DBG_PRINT(x)   DebugSerial.print(x)
  #define DBG_PRINTLN(x) DebugSerial.println(x)
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
//  “恢复出厂”按键：GPIO38 + RESET（只在开机/复位瞬间检查）
//  规则：上电/复位时 GPIO38 = LOW -> 清 NVS 中的 WiFi/配置，并进入 AP 配网
// =======================
#define PIN_FACTORY_RESET 38
#define FACTORY_RESET_ACTIVE_LOW 1
static const uint32_t FACTORY_RESET_SAMPLE_DELAY_MS = 5;

// =======================
//  AP 配置页保底：进入 AP 后 X 分钟没保存配置 -> 睡到“下一个刷新点”
// =======================
static const uint32_t AP_TIMEOUT_MS = 5UL * 60UL * 1000UL; // 5 分钟

// =======================
//  墨水屏参数 & 引脚（Bili 看板：横屏 800x480）
// =======================
static const int EPD_WIDTH  = 800;
static const int EPD_HEIGHT = 480;

// ESP32-S3-N8R8 核心板
#define PIN_EPD_BUSY 14
#define PIN_EPD_RST  13
#define PIN_EPD_DC   12
#define PIN_EPD_CS   11
#define PIN_EPD_SCLK 10
#define PIN_EPD_DIN  9

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
//  Bili Dashboard BIN 路径
// =======================
#define DASHBOARD_PATH "/api/esp32/dashboard.bin"

// =======================
//  配置存储 / WiFi / WebServer
// =======================
Preferences prefs;
WebServer  server(80);

struct Config {
  String  wifi_ssid;
  String  wifi_pass;
  String  backend_hostport;  // host:port 或 http(s)://host:port
  int32_t tz_offset_hours;   // 时区偏移（整数小时），默认 8
  uint8_t refresh_hour;      // 每天的整点小时 0-23
  bool    rotate180;         // 是否旋转 180°
  bool    valid;
};

// 服务器默认空（不泄露隐私）
const char*  DEFAULT_HOSTPORT = "";
const int32_t DEFAULT_TZ      = 8;
const uint8_t DEFAULT_HOUR    = 8;

Config g_cfg;
uint8_t* framebuffer = nullptr;

// =======================
//  启动 hold 的“解锁”：防止上次 deep sleep hold 把自己锁死
// =======================
static void releaseAllGpioHoldsAtBoot() {
  gpio_deep_sleep_hold_dis();
  for (int gpio = 0; gpio <= 48; ++gpio) {
    gpio_num_t gn = (gpio_num_t)gpio;
    if (!GPIO_IS_VALID_GPIO(gn)) continue;
    gpio_hold_dis(gn);
    if (rtc_gpio_is_valid_gpio(gn)) rtc_gpio_hold_dis(gn);
  }
}

// =======================
//  NVS：清空 dashcfg namespace（只在开机瞬间 GPIO38 低电平时调用）
// =======================
static void clearConfigNVS() {
#if DEBUG_LOG
  DBG_PRINTLN("[NVS] clearConfigNVS()");
#endif
  prefs.begin("dashcfg", false);
  prefs.clear();
  prefs.end();
}

// =======================
//  工厂复位检测：只在 setup 开头采样一次
// =======================
static bool isFactoryResetRequestedAtBoot() {
  pinMode(PIN_FACTORY_RESET, INPUT_PULLUP);
  delay(FACTORY_RESET_SAMPLE_DELAY_MS);
#if FACTORY_RESET_ACTIVE_LOW
  return (digitalRead(PIN_FACTORY_RESET) == LOW);
#else
  return (digitalRead(PIN_FACTORY_RESET) == HIGH);
#endif
}

// =======================
//  保存“上次成功 NTP 的时间”（epoch 秒）到 NVS
//  用途：AP 超时后按“下一个 refresh_hour”去睡（没网时只能近似）
// =======================
static void saveLastTimeEpoch(time_t epoch) {
  prefs.begin("dashcfg", false);
  prefs.putULong("last_epoch", (uint32_t)epoch);
  prefs.end();
#if DEBUG_LOG
  DBG_PRINT("[TIME] save last_epoch="); DBG_PRINTLN((uint32_t)epoch);
#endif
}

static bool loadLastTimeEpoch(time_t &epochOut) {
  prefs.begin("dashcfg", true);
  uint32_t v = prefs.getULong("last_epoch", 0);
  prefs.end();
  if (v == 0) return false;
  epochOut = (time_t)v;
  return true;
}

// =======================
//  计算“从 last_epoch 推断的 now”到下一个 refresh_hour 的分钟数
//  注意：没网时只能近似（基于经验/常识的推断）
// =======================
static uint32_t minutesToNextRefreshFromLastEpoch(const Config &cfg) {
  time_t lastEpoch;
  if (!loadLastTimeEpoch(lastEpoch)) {
    // 没任何时钟依据：直接睡 24h 防跑电
    return 1440;
  }

  struct tm t;
  localtime_r(&lastEpoch, &t);

  int curMinOfDay = t.tm_hour * 60 + t.tm_min;
  int targetMin   = (int)cfg.refresh_hour * 60;
  int deltaMin;

  if (curMinOfDay < targetMin) deltaMin = targetMin - curMinOfDay;
  else                         deltaMin = 24 * 60 - (curMinOfDay - targetMin);

  if (deltaMin < 1) deltaMin = 24 * 60;
  if (deltaMin > 1440) deltaMin = 1440;
  return (uint32_t)deltaMin;
}

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
  DBG_PRINTLN("---- loadConfig ----");
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

#if DEBUG_LOG
  DBG_PRINTLN("[CFG] saved");
#endif
}

// =======================
//  HTML 工具
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

// =======================
//  关键修复：进入 AP Portal 前，彻底重置 WiFi 驱动状态，保证 scan 正常（不清 NVS）
// =======================
static void wifiHardResetForPortal() {
#if DEBUG_LOG
  DBG_PRINTLN("[WIFI] wifiHardResetForPortal()");
#endif
  WiFi.scanDelete();
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  delay(200);

  WiFi.mode(WIFI_AP_STA);

  // AP 配网：为了扫描稳定，关省电
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);

  WiFi.scanDelete();
  delay(50);
}

String buildConfigPage() {
  WiFi.scanDelete();
  delay(30);

  int n = WiFi.scanNetworks(/*async=*/false, /*hidden=*/true);

#if DEBUG_LOG
  DBG_PRINT("[CFG] scanNetworks n="); DBG_PRINTLN(n);
#endif

  String curSsid = g_cfg.wifi_ssid;
  String host    = htmlEscape(g_cfg.backend_hostport);
  int32_t tz     = g_cfg.tz_offset_hours;
  if (tz < -12 || tz > 14) tz = DEFAULT_TZ;
  uint8_t hour   = g_cfg.refresh_hour;
  if (hour > 23) hour = DEFAULT_HOUR;
  bool rot180    = g_cfg.rotate180;

  String html;
  html.reserve(4096);

  html += F("<!DOCTYPE html><html><head><meta charset='utf-8'>");
  html += F("<meta name='viewport' content='width=device-width,initial-scale=1'>");
  html += F("<title>Bili-Insight 设置</title></head><body>");
  html += F("<h2>Bili-Insight 设置</h2>");
  html += F("<form method='POST' action='/save'>");

  // 下拉选择 + 手动输入（两个控件）
  html += F("WiFi SSID:<br>");
  html += F("<select id='ssid_select' style='width: 288px;' onchange=\"document.getElementById('ssid_input').value=this.value;\">");
  html += F("<option value=''>（手动输入或选择）</option>");
  if (n > 0) {
    for (int i = 0; i < n; ++i) {
      String s = WiFi.SSID(i);
      if (s.length() == 0) continue;
      String esc = htmlEscape(s);
      html += F("<option value='");
      html += esc;
      html += F("'");
      if (s == curSsid) html += F(" selected");
      html += F(">");
      html += esc;
      html += F("</option>");
    }
  }
  html += F("</select><br>");
  html += F("<input id='ssid_input' name='ssid' style='width: 280px;' value='");
  html += htmlEscape(curSsid);
  html += F("'><br><br>");

  html += F("密码:<br><input name='pass' type='password' style='width: 280px;'><br><br>");

  // 服务器默认空 + 不给示例
  html += F("服务器 (host:port):<br><input name='hostport' size='40' value='");
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
  html += F("</select><br><br>");

  html += F("时区:<br><select name='tz'>");
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
  html += F("</select><br><br>");

  html += F("<label><input type='checkbox' name='rot180' value='1'");
  if (rot180) html += F(" checked");
  html += F("> 画面旋转 180°</label><br><br>");

  if (n <= 0) {
    html += F("<p style='color:#c00'>未扫描到 WiFi，可直接在上方输入框手动填写 SSID。</p>");
  }

  html += F("<input type='submit' value='保存并重启'>");
  html += F("</form></body></html>");

  return html;
}

// =======================
//  WebServer 处理
// =======================
void handleRoot() {
#if DEBUG_LOG
  DBG_PRINTLN("[HTTP] GET /");
#endif
  server.send(200, "text/html; charset=utf-8", buildConfigPage());
}

void handleSave() {
#if DEBUG_LOG
  DBG_PRINTLN("[HTTP] POST /save");
#endif
  String ssid     = server.arg("ssid");
  String pass     = server.arg("pass");
  String host     = server.arg("hostport");
  String hourStr  = server.arg("hour");
  String tzStr    = server.arg("tz");
  bool rot180Req  = (server.arg("rot180") == "1");

  ssid.trim();
  host.trim();

  Config newCfg = g_cfg;

  if (ssid.length() > 0) newCfg.wifi_ssid = ssid;
  if (pass.length() > 0) newCfg.wifi_pass = pass;

  // 允许留空
  newCfg.backend_hostport = host;

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

  delay(800);
  ESP.restart();
}

// =======================
//  Deep Sleep 前：域配置
// =======================
void prepareDeepSleepDomains() {
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH,    ESP_PD_OPTION_OFF);
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_SLOW_MEM,  ESP_PD_OPTION_OFF);
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_FAST_MEM,  ESP_PD_OPTION_OFF);
}

// =======================
//  关闭墨水屏相关引脚，避免漏电流
// =======================
static void powerDownEPD() {
  const int epdPins[] = { PIN_EPD_BUSY, PIN_EPD_RST, PIN_EPD_DC, PIN_EPD_CS, PIN_EPD_SCLK, PIN_EPD_DIN };
  for (size_t i = 0; i < sizeof(epdPins)/sizeof(epdPins[0]); ++i) {
    int p = epdPins[i];
    pinMode(p, INPUT);
    pinMode(p, INPUT_PULLDOWN);
  }
}

// 只 hold EPD 相关脚：别再全局扫 GPIO
static void deepSleepHoldOnlyEpdPins() {
  const int epdPins[] = { PIN_EPD_BUSY, PIN_EPD_RST, PIN_EPD_DC, PIN_EPD_CS, PIN_EPD_SCLK, PIN_EPD_DIN };
  for (size_t i = 0; i < sizeof(epdPins)/sizeof(epdPins[0]); ++i) {
    gpio_num_t gn = (gpio_num_t)epdPins[i];
    if (!GPIO_IS_VALID_GPIO(gn)) continue;

    gpio_set_direction(gn, GPIO_MODE_INPUT);
    gpio_pulldown_en(gn);
    gpio_pullup_dis(gn);
    gpio_hold_en(gn);

    if (rtc_gpio_is_valid_gpio(gn)) rtc_gpio_isolate(gn);
  }
  gpio_deep_sleep_hold_en();
}

// =======================
//  Deep Sleep
// =======================
void goDeepSleepMinutes(uint32_t minutes) {
  if (minutes < 1)    minutes = 1;
  if (minutes > 1440) minutes = 1440;

#if DEBUG_LOG
  DBG_PRINT("[SLEEP] minutes="); DBG_PRINTLN((int)minutes);
#endif

  uint64_t us = (uint64_t)minutes * 60ULL * 1000000ULL;

  if (framebuffer) {
    heap_caps_free(framebuffer);
    framebuffer = nullptr;
  }

  powerDownEPD();

  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  esp_wifi_stop();

#if defined(CONFIG_BT_ENABLED)
  esp_bt_controller_disable();
#endif

  deepSleepHoldOnlyEpdPins();

  prepareDeepSleepDomains();
  esp_sleep_enable_timer_wakeup(us);

#if DEBUG_LOG
  DBG_PRINTLN("[SLEEP] go deep sleep");
#endif
  esp_deep_sleep_start();
}

// =======================
//  启动 AP 配置模式（不清 NVS）
//  保底：X 分钟没保存配置 -> 睡到“下一个刷新点”
// =======================
void startConfigPortal() {
#if DEBUG_LOG
  DBG_PRINTLN("[CFG] enter startConfigPortal()");
#endif

  wifiHardResetForPortal();

  String apSsid     = "BiliDashboard-" + String((uint32_t)ESP.getEfuseMac(), HEX).substring(4);
  const char* apPwd = "12345678";

  bool apOk = WiFi.softAP(apSsid.c_str(), apPwd);

#if DEBUG_LOG
  DBG_PRINT("[CFG] softAP result = "); DBG_PRINTLN(apOk ? "OK" : "FAIL");
  DBG_PRINT("[CFG] AP SSID = "); DBG_PRINTLN(apSsid);
  DBG_PRINT("[CFG] AP IP   = "); DBG_PRINTLN(WiFi.softAPIP());
#endif

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();

  uint32_t enterMs = millis();

  for (;;) {
    server.handleClient();

    if (millis() - enterMs > AP_TIMEOUT_MS) {
#if DEBUG_LOG
      DBG_PRINTLN("[AP] timeout: no config saved");
#endif
      uint32_t mins = minutesToNextRefreshFromLastEpoch(g_cfg);
#if DEBUG_LOG
      DBG_PRINT("[AP] sleep to next refresh, minutes="); DBG_PRINTLN((int)mins);
#endif
      delay(50);
      goDeepSleepMinutes(mins);
    }

    delay(10);
  }
}

// =======================
//  WiFi 连接
// =======================
bool connectWiFi(const Config &cfg, uint32_t timeout_ms = 15000) {
#if DEBUG_LOG
  DBG_PRINTLN("[WIFI] connectWiFi()");
  DBG_PRINT("[WIFI] target ssid="); DBG_PRINTLN(cfg.wifi_ssid);
#endif

  if (cfg.wifi_ssid.isEmpty()) return false;

  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_STA);

  WiFi.setSleep(true);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  WiFi.begin(cfg.wifi_ssid.c_str(), cfg.wifi_pass.c_str());

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeout_ms) {
    delay(200);
#if DEBUG_LOG
    DBG_PRINT(".");
#endif
  }
#if DEBUG_LOG
  DBG_PRINTLN();
#endif

  bool ok = (WiFi.status() == WL_CONNECTED);

#if DEBUG_LOG
  if (ok) {
    DBG_PRINTLN("[WIFI] connected");
    DBG_PRINT("[WIFI] IP="); DBG_PRINTLN(WiFi.localIP());
  } else {
    DBG_PRINTLN("[WIFI] connect FAILED");
  }
#endif

  return ok;
}

// =======================
//  NTP 同步时间
// =======================
bool syncTime(const Config &cfg, struct tm &outLocal) {
#if DEBUG_LOG
  DBG_PRINTLN("[TIME] syncTime start");
#endif
  long offsetSec = (long)cfg.tz_offset_hours * 3600;
  configTime(offsetSec, 0, "pool.ntp.org", "time.nist.gov", "ntp.aliyun.com");

  for (int i = 0; i < 30; ++i) {
    if (getLocalTime(&outLocal)) {
#if DEBUG_LOG
      char buf[64];
      strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &outLocal);
      DBG_PRINT("[TIME] OK: "); DBG_PRINTLN(buf);
#endif
      time_t nowEpoch = time(nullptr);
      if (nowEpoch > 0) saveLastTimeEpoch(nowEpoch);
      return true;
    }
    delay(500);
  }
#if DEBUG_LOG
  DBG_PRINTLN("[TIME] syncTime FAILED");
#endif
  return false;
}

// =======================
//  HTTP 下载 dashboard.bin 到 framebuffer（800x480, 1字节/像素：0黑1白2红3黄）
// =======================
bool downloadDashboardBin(const Config &cfg) {
  size_t target = (size_t)EPD_WIDTH * EPD_HEIGHT; // 384000 bytes

  if (!framebuffer) {
#if DEBUG_LOG
    DBG_PRINT("[FB] malloc framebuffer size="); DBG_PRINTLN((int)target);
#endif
    framebuffer = (uint8_t*)heap_caps_malloc(
      target,
      MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM
    );
    if (!framebuffer) {
#if DEBUG_LOG
      DBG_PRINTLN("[FB] malloc PSRAM failed, try internal RAM");
#endif
      framebuffer = (uint8_t*)heap_caps_malloc(target, MALLOC_CAP_8BIT);
    }
  }
  if (!framebuffer) {
#if DEBUG_LOG
    DBG_PRINTLN("[FB] framebuffer malloc FAILED");
#endif
    return false;
  }

  if (cfg.backend_hostport.length() == 0) {
#if DEBUG_LOG
    DBG_PRINTLN("[HTTP] hostport empty, skip download");
#endif
    return false;
  }

  // 规范化 URL：支持 host:port 或 http(s)://host:port
  String hp = cfg.backend_hostport;
  hp.trim();

  String url;
  if (hp.startsWith("http://") || hp.startsWith("https://")) {
    // 如果用户填的是 base（没有路径），就补上 DASHBOARD_PATH
    if (hp.indexOf("/") == hp.indexOf("://") + 3) {
      url = hp + String(DASHBOARD_PATH);
    } else {
      // 用户可能直接填了完整路径，那就直接用
      url = hp;
    }
  } else {
    url = "http://" + hp + String(DASHBOARD_PATH);
  }

#if DEBUG_LOG
  DBG_PRINT("[HTTP] GET "); DBG_PRINTLN(url);
#endif

  HTTPClient http;
  http.begin(url);
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
#if DEBUG_LOG
    DBG_PRINT("[HTTP] code="); DBG_PRINTLN(code);
#endif
    http.end();
    return false;
  }

  int len = http.getSize();
#if DEBUG_LOG
  DBG_PRINT("[HTTP] content-length="); DBG_PRINTLN(len);
#endif

  WiFiClient *stream = http.getStreamPtr();
  size_t total = 0;

  const uint32_t DOWNLOAD_TIMEOUT_MS = 60 * 1000;
  uint32_t start_ms = millis();

  while (http.connected() && (len > 0 || len == -1) && total < target) {
    if (millis() - start_ms > DOWNLOAD_TIMEOUT_MS) {
#if DEBUG_LOG
      DBG_PRINTLN("[HTTP] download timeout");
#endif
      http.end();
      return false;
    }

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

#if DEBUG_LOG
  DBG_PRINT("[HTTP] total read="); DBG_PRINTLN((int)total);
#endif

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
//  墨水屏显示（横屏 800x480，旋转只用 setRotation(0/2)）
// =======================
void initDisplay(const Config &cfg) {
#if DEBUG_LOG
  DBG_PRINTLN("[EPD] initDisplay");
#endif
  SPI.end();
  SPI.begin(PIN_EPD_SCLK, -1 /*MISO*/, PIN_EPD_DIN, PIN_EPD_CS);

  display.init(0, true, 2, false);

  // 横屏：rotation=0 正向；rotation=2 旋转180
  if (cfg.rotate180) display.setRotation(2);
  else              display.setRotation(0);

#if DEBUG_LOG
  DBG_PRINT("[EPD] rotation="); DBG_PRINTLN(cfg.rotate180 ? 2 : 0);
#endif
}

void drawFromFramebuffer(const Config &cfg) {
  (void)cfg;

  display.setFullWindow();

  int w = display.width();   // rotation=0 应为 800
  int h = display.height();  // rotation=0 应为 480

#if DEBUG_LOG
  DBG_PRINT("[EPD] logical w="); DBG_PRINT(w);
  DBG_PRINT(" h="); DBG_PRINTLN(h);
#endif

  display.firstPage();
  do {
    for (int y = 0; y < EPD_HEIGHT && y < h; ++y) {
      for (int x = 0; x < EPD_WIDTH && x < w; ++x) {
        uint8_t c = framebuffer[y * EPD_WIDTH + x];

        uint16_t col;
        switch (c) {
          case 0: col = GxEPD_BLACK;  break;
          case 1: col = GxEPD_WHITE;  break;
          case 2: col = GxEPD_RED;    break;
          case 3: col = GxEPD_YELLOW; break;
          default: col = GxEPD_WHITE; break;
        }

        display.drawPixel(x, y, col);
      }
    }
  } while (display.nextPage());

  display.hibernate();
}

// =======================
//  睡到下一个整点刷新
// =======================
void sleepUntilNextSchedule(const Config &cfg, bool hasTime, const struct tm &now) {
  if (!hasTime) {
    goDeepSleepMinutes(1440);
    return;
  }

  int curMinOfDay = now.tm_hour * 60 + now.tm_min;
  int targetMin   = (int)cfg.refresh_hour * 60;
  int delta;

  if (curMinOfDay < targetMin) delta = targetMin - curMinOfDay;
  else                         delta = 24 * 60 - (curMinOfDay - targetMin);

  if (delta < 1) delta = 24 * 60;

#if DEBUG_LOG
  DBG_PRINT("[SLEEP] nowMin="); DBG_PRINT(curMinOfDay);
  DBG_PRINT(" targetMin="); DBG_PRINT(targetMin);
  DBG_PRINT(" delta="); DBG_PRINTLN(delta);
#endif

  goDeepSleepMinutes((uint32_t)delta);
}

// =======================
//  setup / loop
// =======================
void setup() {
  releaseAllGpioHoldsAtBoot();

  setCpuFrequencyMhz(80);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  DBG_BEGIN();
  delay(200);

#if DEBUG_LOG
  DBG_PRINTLN();
  DBG_PRINTLN("===== ESP32-S3 Bili-Insight Dashboard boot =====");
#endif

  if (isFactoryResetRequestedAtBoot()) {
#if DEBUG_LOG
    DBG_PRINTLN("[BOOT] GPIO38 LOW at boot -> clear NVS");
#endif
    clearConfigNVS();
  }

  randomSeed(esp_random());

  loadConfig(g_cfg);

  if (!g_cfg.valid) {
#if DEBUG_LOG
    DBG_PRINTLN("[BOOT] no valid config -> AP portal");
#endif
    startConfigPortal();
  }

#if DEBUG_LOG
  DBG_PRINTLN("[BOOT] have config -> connect WiFi");
#endif
  if (!connectWiFi(g_cfg)) {
#if DEBUG_LOG
    DBG_PRINTLN("[BOOT] connect failed -> AP portal");
#endif
    startConfigPortal();
  }

  struct tm timeinfo;
  bool hasTime = syncTime(g_cfg, timeinfo);

  bool ok = downloadDashboardBin(g_cfg);
  if (ok) {
    initDisplay(g_cfg);
    drawFromFramebuffer(g_cfg);
  } else {
#if DEBUG_LOG
    DBG_PRINTLN("[BOOT] downloadDashboardBin FAILED");
#endif
  }

  if (!hasTime) {
    struct tm tmp;
    if (syncTime(g_cfg, tmp)) sleepUntilNextSchedule(g_cfg, true, tmp);
    else                      sleepUntilNextSchedule(g_cfg, false, timeinfo);
  } else {
    sleepUntilNextSchedule(g_cfg, true, timeinfo);
  }
}

void loop() {
  // 不使用
}