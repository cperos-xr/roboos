// Robot face + dashboard firmware for the ESP32-2432S028R "Cheap Yellow Display".
// Talks to the Orange Pi supervisor (robotd) over the CH340 USB serial at 921600 baud using
// line-delimited JSON ({"t":"type",...}). Images arrive as a JSON header {"t":"img","len":N,...}
// followed by exactly N raw JPEG bytes; every image is acknowledged with {"t":"ack","id":..}.
// Build: Arduino IDE / arduino-cli, board "ESP32 Dev Module" (esp32:esp32:esp32), libs LovyanGFX + ArduinoJson.
#include "common.h"
#include <Preferences.h>
#include "driver/dac_cosine.h"
#include "esp_system.h"
#include "mbedtls/base64.h"

LGFX lcd;

// ------------------------------------------------------------------ themes
struct Theme { const char* name; uint32_t bg, panel, panel2, line, text, dim, accent, btn, sel, ok, warn, bad, gray, blue; };
static const Theme THEMES[] = {
  //  name              bg        panel     panel2    line      text      dim       accent    button    select    ok        warn      bad       gray      blue
  {"Original",       0x000000, 0x161B26, 0x232B3B, 0x3A4458, 0xE8ECF4, 0x8A94A8, 0x00D0FF, 0x1F6FEB, 0x1F4F8B, 0x30D158, 0xFFB020, 0xFF453A, 0x5A6478, 0x3D8BFF},
  {"Light",          0xF0F2F6, 0xFFFFFF, 0xDDE3EC, 0xB8C2D0, 0x18202C, 0x5A6474, 0x0A6FD6, 0x1F6FEB, 0xBBD3FF, 0x1E9E44, 0xC77700, 0xD32F2F, 0x98A2B0, 0x1F6FEB},
  {"Dark",           0x1E1E1E, 0x252526, 0x333337, 0x4A4A4A, 0xD4D4D4, 0x8A8A8A, 0x3794FF, 0x0E639C, 0x264F78, 0x89D185, 0xCCA700, 0xF14C4C, 0x6E6E6E, 0x3794FF},
  {"Solarized Dark", 0x002B36, 0x073642, 0x0B4350, 0x285A66, 0x93A1A1, 0x657B83, 0x2AA198, 0x268BD2, 0x0F5566, 0x859900, 0xB58900, 0xDC322F, 0x586E75, 0x268BD2},
  {"Darcula",        0x2B2B2B, 0x313335, 0x3C3F41, 0x55595C, 0xA9B7C6, 0x808080, 0xCC7832, 0x3592C4, 0x214283, 0x6A8759, 0xBBB529, 0xBC3F3C, 0x5E6060, 0x6897BB},
  {"Monokai",        0x272822, 0x2F302A, 0x3E3D32, 0x57564A, 0xF8F8F2, 0x8F8B76, 0x66D9EF, 0xAE81FF, 0x49483E, 0xA6E22E, 0xFD971F, 0xF92672, 0x75715E, 0x66D9EF},
};
const int N_THEMES = sizeof(THEMES) / sizeof(THEMES[0]);
uint32_t C_BG, C_PANEL, C_PANEL2, C_LINE, C_TEXT, C_DIM, C_ACCENT, C_BTN, C_SEL, C_OK, C_WARN, C_BAD, C_GRAY, C_BLUE;
void applyTheme(int i) {
  const Theme& t = THEMES[(i >= 0 && i < N_THEMES) ? i : 0];
  C_BG = t.bg; C_PANEL = t.panel; C_PANEL2 = t.panel2; C_LINE = t.line; C_TEXT = t.text; C_DIM = t.dim; C_ACCENT = t.accent;
  C_BTN = t.btn; C_SEL = t.sel; C_OK = t.ok; C_WARN = t.warn; C_BAD = t.bad; C_GRAY = t.gray; C_BLUE = t.blue;
}
const char* themeName(int i) { return THEMES[(i >= 0 && i < N_THEMES) ? i : 0].name; }
LGFX_Sprite canvas(&lcd);
LGFX_Sprite bar(&lcd);
Preferences prefs;
Settings S;
PageId page = P_FACE;
bool dirty = true, barDirty = true;
uint32_t lastRxMs = 0, rxCount = 0, rxErr = 0;
String piTime = "--:--", panelInfo = "?", brainLabel = "AI: ?", visionLabel = "OFF";
// battery from the Pi's INA219 ({"t":"batt","p":%,"v":volts,"s":"ok|low|crit|chg|ext|none"}); hidden until the first message
static int battPct = -1;
static float battV = 0;
static String battState = "none";
static String battTxt = "";        // label chosen on the Pi (Settings > Battery > Top bar shows): "72%", "1h27", "11.8V"
static uint32_t battRxMs = 0;
uint8_t* imgBuf = nullptr;
uint8_t* prevBuf = nullptr;
size_t imgMax = 0, prevMax = 0;

// Take the biggest image buffers that fit, and tell the Pi (hello img_max / prev_max) so JPEGs are sized to them.
static void allocBuffers() {
  static const size_t imgSizes[] = {IMG_MAX, 40 * 1024, 32 * 1024, 24 * 1024, 16 * 1024};
  for (size_t sz : imgSizes)
    if (ESP.getMaxAllocHeap() >= sz && (imgBuf = (uint8_t*)malloc(sz))) { imgMax = sz; break; }
  static const size_t prevSizes[] = {PREV_MAX, 12 * 1024, 8 * 1024};
  for (size_t sz : prevSizes)
    if (ESP.getMaxAllocHeap() >= sz && (prevBuf = (uint8_t*)malloc(sz))) { prevMax = sz; break; }
}
size_t prevLen = 0;
bool screenAsleep = false;
bool canvasOk = false;

// Bluesky numbers for the face page's top-left corner (sent by the Pi: {"t":"social",...})
static bool socOk = false;
static int socLikes = 0, socFollowers = 0, socPosts = 0;

// Screenshot: read the LCD back row by row and send it to the Pi as base64 RGB565 ({"t":"shot","y":..,"d":..}).
// Takes about 2.5 s at 921600 baud; nothing else runs meanwhile. Uses ~1.5 KB of static buffers.
static void screenshotSend() {
  static uint16_t row[SCR_W];
  static unsigned char b64[((SCR_W * 2 + 2) / 3) * 4 + 4];
  for (int y = 0; y < SCR_H; y++) {
    lcd.readRect(0, y, SCR_W, 1, row);
    size_t olen = 0;
    mbedtls_base64_encode(b64, sizeof(b64), &olen, (const unsigned char*)row, SCR_W * 2);
    Serial.print("{\"t\":\"shot\",\"y\":"); Serial.print(y); Serial.print(",\"d\":\"");
    Serial.write(b64, olen);
    Serial.print("\"}\n");
  }
  Serial.print("{\"t\":\"shot_end\",\"w\":"); Serial.print(SCR_W); Serial.print(",\"h\":"); Serial.print(SCR_H); Serial.print("}\n");
}
JsonDocument statusDoc, devDoc, testsDoc, filesDoc, ideasDoc;
JsonDocument setDoc;

static const int PIN_LED_R = 4, PIN_LED_G = 16, PIN_LED_B = 17, PIN_LDR = 34;
static bool helloAcked = false;
static uint32_t bootMs = 0;

// ------------------------------------------------------------------ CRC32 (zlib compatible)
static uint32_t crcTable[256];
static void crcInit() {
  for (uint32_t i = 0; i < 256; i++) {
    uint32_t c = i;
    for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
    crcTable[i] = c;
  }
}
static uint32_t crc32(const uint8_t* p, size_t n) {
  uint32_t c = 0xFFFFFFFFu;
  while (n--) c = crcTable[(c ^ *p++) & 0xFF] ^ (c >> 8);
  return c ^ 0xFFFFFFFFu;
}

// ------------------------------------------------------------------ settings
void putSettings(JsonObject o) {
  o["bright"] = S.bright; o["autob"] = S.autoBright; o["vol"] = S.vol; o["theme"] = S.theme;
  o["rot180"] = S.rot180; o["invert"] = S.invert; o["st7789"] = S.st7789;
}
void saveSettings() {
  prefs.begin("cyd", false);
  prefs.putUChar("bright", S.bright);
  prefs.putBool("autob", S.autoBright);
  prefs.putUChar("vol", S.vol);
  prefs.putBool("rot", S.rot180);
  prefs.putBool("inv", S.invert);
  prefs.putBool("st", S.st7789);
  prefs.putBool("calok", S.calOk);
  prefs.putUChar("theme", S.theme);
  prefs.putBytes("cal", S.cal, sizeof(S.cal));
  prefs.putBool("affok", S.affOk);
  prefs.putBytes("aff", S.aff, sizeof(S.aff));
  JsonDocument c; c["t"] = "cyd_cfg"; putSettings(c.to<JsonObject>()); sendJson(c);
  prefs.end();
}
static void loadSettings() {
  prefs.begin("cyd", true);
  S.bright = prefs.getUChar("bright", 180);
  S.autoBright = prefs.getBool("autob", false);
  S.vol = prefs.getUChar("vol", 1);
  S.rot180 = prefs.getBool("rot", false);
  S.invert = prefs.getBool("inv", false);
  S.st7789 = prefs.getBool("st", false);
  S.calOk = prefs.getBool("calok", false);
  S.theme = prefs.getUChar("theme", 0);
  if (S.calOk) prefs.getBytes("cal", S.cal, sizeof(S.cal));
  S.affOk = prefs.getBool("affok", false);
  if (S.affOk) prefs.getBytes("aff", S.aff, sizeof(S.aff));
  prefs.end();
}
void applyDisplaySettings() {
  lcd.setRotation(S.rot180 ? 3 : 1);
  lcd.invertDisplay(S.invert);
  if (!screenAsleep) lcd.setBrightness(S.bright);
  if (S.affOk) lcd.getPanel()->setCalibrateAffine(S.aff);
  else if (S.calOk) lcd.setTouchCalibrate(S.cal);
}
// 5-point touch calibration. Targets sit 30 px inside the edges (corner pixels are under the bezel and
// the least accurate part of a resistive panel). Each tap averages ~16 stable raw readings, then a
// least-squares affine fit maps raw XPT2046 values to native (portrait 240x320) panel coordinates.
static bool sampleRaw(int& rx, int& ry, uint32_t timeoutMs) {
  lgfx::touch_point_t tp, tp2;
  uint32_t t0 = millis();
  while (!lcd.getTouchRaw(&tp)) { if (millis() - t0 > timeoutMs) return false; delay(3); }
  delay(60);  // let the finger settle
  long sx = 0, sy = 0; int n = 0;
  uint32_t t1 = millis();
  while (n < 16 && millis() - t1 < 3000) {
    if (!lcd.getTouchRaw(&tp)) { delay(3); continue; }
    delay(6);
    if (!lcd.getTouchRaw(&tp2) || abs(tp.x - tp2.x) > 25 || abs(tp.y - tp2.y) > 25) continue;
    sx += tp.x + tp2.x; sy += tp.y + tp2.y; n += 2;
  }
  uint32_t r0 = millis();
  while (lcd.getTouchRaw(&tp) && millis() - r0 < 5000) delay(5);  // wait for release
  delay(200);
  if (n < 8) return false;
  rx = sx / n; ry = sy / n;
  return true;
}
static bool solve3(double m[3][3], double v[3], double out[3]) {
  double d = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  if (fabs(d) < 1e-9) return false;
  for (int c = 0; c < 3; c++) {
    double t[3][3];
    for (int r = 0; r < 3; r++) for (int k = 0; k < 3; k++) t[r][k] = (k == c) ? v[r] : m[r][k];
    out[c] = (t[0][0] * (t[1][1] * t[2][2] - t[1][2] * t[2][1]) - t[0][1] * (t[1][0] * t[2][2] - t[1][2] * t[2][0]) + t[0][2] * (t[1][0] * t[2][1] - t[1][1] * t[2][0])) / d;
  }
  return true;
}
static void crossNative(int x, int y, uint32_t c) {
  lcd.drawFastHLine(x - 14, y, 29, c); lcd.drawFastVLine(x, y - 14, 29, c);
  lcd.drawCircle(x, y, 6, c);
}
static void runCalibration() {
  const int NX[5] = {30, 209, 30, 209, 120}, NY[5] = {30, 30, 289, 289, 160};
  int rx[5], ry[5];
  bool ok = true;
  lcd.setRotation(S.rot180 ? 3 : 1);
  lcd.fillScreen((uint32_t)C_BG);
  lcd.setTextColor((uint32_t)C_TEXT, (uint32_t)C_BG);
  lcd.setTextDatum(MC_DATUM);
  lcd.setFont(&fonts::FreeSansBold9pt7b);
  lcd.drawString("Touch calibration", SCR_W / 2, SCR_H / 2 - 44);
  lcd.setFont(&fonts::Font2);
  lcd.drawString("Tap the CENTRE of each yellow +", SCR_W / 2, SCR_H / 2 + 36);
  lcd.drawString("(a fingernail or stylus works best)", SCR_W / 2, SCR_H / 2 + 54);
  lcd.setRotation(0);  // native panel orientation, same frame the affine maps into
  for (int i = 0; i < 5 && ok; i++) {
    crossNative(NX[i], NY[i], (uint32_t)C_WARN);
    ok = sampleRaw(rx[i], ry[i], 60000);
    crossNative(NX[i], NY[i], ok ? (uint32_t)C_OK : (uint32_t)C_BAD);
  }
  float aff[6]; int worst = 999;
  if (ok) {
    double m[3][3] = {{0}}, vx[3] = {0}, vy[3] = {0}, ax[3], ay[3];
    for (int i = 0; i < 5; i++) {
      double p[3] = {(double)rx[i], (double)ry[i], 1.0};
      for (int r = 0; r < 3; r++) { for (int c = 0; c < 3; c++) m[r][c] += p[r] * p[c]; vx[r] += p[r] * NX[i]; vy[r] += p[r] * NY[i]; }
    }
    ok = solve3(m, vx, ax) && solve3(m, vy, ay);
    if (ok) {
      worst = 0;
      for (int i = 0; i < 5; i++) {
        double ex = ax[0] * rx[i] + ax[1] * ry[i] + ax[2] - NX[i], ey = ay[0] * rx[i] + ay[1] * ry[i] + ay[2] - NY[i];
        worst = max(worst, (int)sqrt(ex * ex + ey * ey));
      }
      for (int k = 0; k < 3; k++) { aff[k] = ax[k]; aff[3 + k] = ay[k]; }
      ok = worst <= 25;
    }
  }
  lcd.setRotation(S.rot180 ? 3 : 1);
  lcd.fillScreen((uint32_t)C_BG);
  lcd.setFont(&fonts::FreeSansBold9pt7b);
  char b[64];
  if (ok) {
    memcpy(S.aff, aff, sizeof(aff)); S.affOk = true; S.calOk = true;
    saveSettings();
    lcd.getPanel()->setCalibrateAffine(S.aff);
    snprintf(b, sizeof(b), "Calibrated (fit error %d px)", worst);
    lcd.setTextColor((uint32_t)C_OK, (uint32_t)C_BG);
  } else {
    snprintf(b, sizeof(b), worst < 999 ? "Taps inconsistent (%d px) - kept old" : "Calibration cancelled - kept old", worst);
    lcd.setTextColor((uint32_t)C_WARN, (uint32_t)C_BG);
  }
  lcd.drawString(b, SCR_W / 2, SCR_H / 2);
  JsonDocument d; d["t"] = "touch_cal"; d["ok"] = ok; d["fit_px"] = worst; sendJson(d);
  delay(1500);
  lcd.fillScreen((uint32_t)C_BG);
  dirty = barDirty = true;
  if (ok) goPage(P_TOUCHTEST);   // verify straight away
}
void runCalibrationPublic() { runCalibration(); }

// ------------------------------------------------------------------ serial out
void sendJson(JsonDocument& d) {
  serializeJson(d, Serial);
  Serial.write('\n');
}
void sendEvt(const char* t) {
  JsonDocument d; d["t"] = t; sendJson(d);
}
void sendKV(const char* t, const char* k, const char* v) {
  JsonDocument d; d["t"] = t; d[k] = v; sendJson(d);
}
static void sendHello() {
  JsonDocument d;
  d["t"] = "hello"; d["fw"] = FW_NAME; d["ver"] = FW_VER; d["panel"] = panelInfo;
  d["st7789"] = S.st7789; d["w"] = SCR_W; d["h"] = SCR_H; d["cw"] = CW; d["ch"] = CH;
  d["img_max"] = imgMax; d["prev_max"] = prevMax; d["heap"] = ESP.getFreeHeap(); d["reset"] = (int)esp_reset_reason();
  d["shot"] = 1;   // can send screenshots
  d["gbf"] = 1;    // draws Game Boy frames (RoboOS Game Boy app)
  d["theme"] = themeName(S.theme); d["canvas"] = canvasOk; d["calibrated"] = S.affOk ? "affine5" : (S.calOk ? "lgfx4" : "none"); d["page"] = pageName(page);
  putSettings(d["set"].to<JsonObject>());
  sensorsHello(d);
  sendJson(d);
}
static void sendHeartbeat() {
  JsonDocument d;
  d["t"] = "hb"; d["page"] = pageName(page); d["up"] = millis() / 1000; d["heap"] = ESP.getFreeHeap();
  d["minheap"] = ESP.getMinFreeHeap(); d["lux"] = readLight(); d["rx"] = rxCount; d["rxerr"] = rxErr;
  d["mood"] = (int)curMood; d["asleep"] = screenAsleep;
  sendJson(d);
}
bool linkUp() { return lastRxMs && millis() - lastRxMs < 6000; }

// ------------------------------------------------------------------ serial in
static char lineBuf[LINE_MAX];
static size_t lineLen = 0;
static bool lineOverflow = false;
static bool binMode = false, binDiscard = false;
static size_t binGot = 0, binLen = 0;
static uint32_t binStart = 0;
static JsonDocument imgMeta;

static void sendAck(uint32_t id, bool ok, const char* why) {
  JsonDocument d; d["t"] = "ack"; d["id"] = id; d["ok"] = ok;
  if (why) d["why"] = why;
  sendJson(d);
}
static void beginImage(JsonDocument& d) {
  binLen = d["len"] | 0;
  binGot = 0; binStart = millis(); binMode = true;
  binDiscard = (binLen == 0 || binLen > imgMax || !imgBuf || audioBusy());
  imgMeta = d;
}
static void endImage() {
  binMode = false;
  uint32_t id = imgMeta["id"] | 0;
  if (binDiscard) { sendAck(id, false, "too_big"); return; }
  uint32_t want = imgMeta["crc"] | 0;
  if (want && crc32(imgBuf, binLen) != want) { rxErr++; sendAck(id, false, "crc"); return; }
  if (!strcmp(imgMeta["t"] | "", "pcm")) { audioReceived(imgMeta, imgBuf, binLen, id); return; }  // acks itself
  if (!strcmp(imgMeta["t"] | "", "gbf")) {           // Game Boy rows: ok = drawn, so the Pi knows to resend them all
    bool shown = gbFrame(imgMeta, imgBuf, binLen);
    sendAck(id, shown, shown ? nullptr : "not_shown");
    return;
  }
  bool drawn = imageReceived(imgMeta, imgBuf, binLen);
  sendAck(id, true, drawn ? nullptr : "not_shown");
}

static void handleLine(const char* s, size_t n) {
  if (s[0] != '{') return;  // ROM boot messages / noise
  JsonDocument d;
  if (deserializeJson(d, s, n)) { rxErr++; return; }
  lastRxMs = millis(); rxCount++;
  const char* t = d["t"] | "";
  if (!helloAcked) { helloAcked = true; barDirty = true; }

  if (!strcmp(t, "img") || !strcmp(t, "pcm") || !strcmp(t, "gbf")) { beginImage(d); return; }
  if (!strcmp(t, "brain")) { brainLabel = (const char*)(d["label"] | "?"); if (!(d["online"] | false)) brainLabel += " (no key)"; if (page == P_SETTINGS) dirty = true; return; }
  if (!strcmp(t, "vision_mode")) { visionLabel = (const char*)(d["mode"] | "off"); visionLabel.toUpperCase(); if (page == P_CAMERA) dirty = true; return; }
  if (!strcmp(t, "qr")) { showQr(d["text"] | "", d["title"] | "", d["sub"] | ""); return; }
  if (!strcmp(t, "listen_state")) { listening = d["on"] | false; dirty = true; return; }
  if (sensorsMsg(t, d)) return;
  if (!strcmp(t, "time")) { piTime = (const char*)(d["hm"] | "--:--"); barDirty = true; return; }
  if (!strcmp(t, "batt")) {
    battPct = d["p"] | -1; battV = d["v"] | 0.0f; battState = (const char*)(d["s"] | "none"); battTxt = (const char*)(d["txt"] | ""); battRxMs = millis();
    barDirty = true; return;
  }
  if (!strcmp(t, "face")) { faceMsg(d); return; }
  if (!strcmp(t, "imu")) { imuMsg(d); return; }
  if (!strcmp(t, "status")) { statusDoc = d; if (page == P_STATUS) dirty = true; return; }
  if (!strcmp(t, "devices")) { devDoc = d; if (page == P_DEVICES) dirty = true; return; }
  if (!strcmp(t, "tests")) { testsDoc = d; if (page == P_TESTS) dirty = true; return; }
  if (!strcmp(t, "test_result")) { testResult(d); return; }
  if (!strcmp(t, "files")) { filesDoc = d; filesReceived(); return; }
  if (!strcmp(t, "ideas")) { ideasDoc = d; if (page == P_IDEAS) dirty = true; return; }
  if (!strcmp(t, "settings")) { setDoc = d; if (page == P_SETTINGS) dirty = true; return; }
  if (!strcmp(t, "logs")) { logsSet(d); return; }
  if (!strcmp(t, "log")) { logsAdd(d["src"] | "robot", d["line"] | ""); return; }
  if (!strcmp(t, "toast")) { toast(String((const char*)(d["msg"] | "")), d["ms"] | 2500); return; }
  if (!strcmp(t, "ask")) {
    JsonArray o = d["opts"].as<JsonArray>();
    const char* o1 = o.size() > 0 ? (const char*)o[0] : "OK";
    const char* o2 = o.size() > 1 ? (const char*)o[1] : nullptr;
    const char* o3 = o.size() > 2 ? (const char*)o[2] : nullptr;
    if (screenAsleep) { screenAsleep = false; applyDisplaySettings(); }
    modalAsk(String((const char*)(d["id"] | "")), String((const char*)(d["title"] | "Question")),
             String((const char*)(d["q"] | "")), o1, o2, o3, false);
    playChime(1);
    return;
  }
  if (!strcmp(t, "page")) {
    const char* n = d["name"] | "face";
    for (int i = 0; i < P_COUNT; i++) if (!strcmp(n, pageName((PageId)i))) { goPage((PageId)i); break; }
    return;
  }
  if (!strcmp(t, "beep")) {
    if (d["chime"].is<int>()) playChime(d["chime"].as<int>());
    else playTone(d["hz"] | 1000, d["ms"] | 120);
    return;
  }
  if (!strcmp(t, "lips")) { lipsStart(d["lv"] | "", d["ms"] | 40); return; }
  if (!strcmp(t, "shot")) { screenshotSend(); return; }
  if (!strcmp(t, "social")) {
    socLikes = d["likes"] | 0; socFollowers = d["followers"] | 0; socPosts = d["posts"] | 0; socOk = true;
    if (page == P_FACE) barDirty = true;
    return;
  }
  if (!strcmp(t, "led")) { setLed(d["r"] | 0, d["g"] | 0, d["b"] | 0); return; }
  if (!strcmp(t, "cfg")) {
    if (d["bright"].is<int>()) S.bright = d["bright"];
    if (d["vol"].is<int>()) S.vol = d["vol"];
    if (d["invert"].is<bool>()) S.invert = d["invert"];
    if (d["rot180"].is<bool>()) S.rot180 = d["rot180"];
    if (d["theme"].is<int>()) { S.theme = constrain(d["theme"].as<int>(), 0, N_THEMES - 1); applyTheme(S.theme); }
    if (d["sleep"].is<bool>()) { screenAsleep = d["sleep"]; lcd.setBrightness(screenAsleep ? 0 : S.bright); }
    if (d["autob"].is<bool>()) S.autoBright = d["autob"];
    bool panel = d["st7789"].is<bool>() && d["st7789"].as<bool>() != S.st7789;
    if (panel) S.st7789 = d["st7789"];
    saveSettings(); applyDisplaySettings(); dirty = barDirty = true;
    if (panel) {               // a different display driver needs a restart (as the old local Panel driver button)
      Preferences p; p.begin("cyd", false); p.putBool("panelset", true); p.end();
      delay(50); ESP.restart();
    }
    return;
  }
  if (!strcmp(t, "calibrate")) { runCalibration(); return; }
  if (!strcmp(t, "hello_ack") || !strcmp(t, "hello?")) { if (!strcmp(t, "hello?")) sendHello(); return; }
  if (!strcmp(t, "ping")) { JsonDocument r; r["t"] = "pong"; r["id"] = d["id"] | 0; r["up"] = millis(); sendJson(r); return; }
  if (!strcmp(t, "restart")) { delay(50); ESP.restart(); }
}

static void serialPoll() {
  int budget = 24576;
  while (budget > 0) {
    int avail = Serial.available();
    if (avail <= 0) break;
    if (binMode) {
      size_t need = binLen - binGot;
      size_t n = (size_t)avail < need ? (size_t)avail : need;
      if (binDiscard) { for (size_t i = 0; i < n; i++) Serial.read(); }
      else n = Serial.readBytes(imgBuf + binGot, n);
      binGot += n; budget -= n;
      if (binGot >= binLen) endImage();
      continue;
    }
    int c = Serial.read(); budget--;
    if (c == '\n') {
      if (!lineOverflow && lineLen) { lineBuf[lineLen] = 0; handleLine(lineBuf, lineLen); }
      lineLen = 0; lineOverflow = false;
    } else if (c != '\r') {
      if (lineLen < LINE_MAX - 1) lineBuf[lineLen++] = (char)c; else lineOverflow = true;
    }
  }
  if (binMode && millis() - binStart > 4000) {
    binMode = false; rxErr++;
    sendAck(imgMeta["id"] | 0, false, "timeout");
  }
}

// ------------------------------------------------------------------ pages / navigation
static const char* PAGE_NAMES[P_COUNT] = {
  "face", "menu", "status", "devices", "tests", "camera", "photos", "viewer",
  "imu", "logs", "ideas", "settings", "about", "touchtest", "desktop"
};
static const char* PAGE_TITLES[P_COUNT] = {
  "", "Menu", "Status", "Devices", "Tests", "Camera", "Photos", "Photo",
  "IMU", "Logs", "Ideas", "Settings", "About", "Touch test", "RoboOS"
};
const char* pageName(PageId p) { return p < P_COUNT ? PAGE_NAMES[p] : "?"; }

static PageId backTarget(PageId p) {
  switch (p) {
    case P_FACE: return P_MENU;
    case P_MENU: return P_FACE;
    case P_VIEWER: return P_PHOTOS;
    case P_TOUCHTEST: return P_TESTS;
    case P_DESKTOP: return P_MENU;
    default: return P_MENU;
  }
}

void goPage(PageId p) {
  if (p >= P_COUNT) return;
  page = p;
  imageOverlay = false;
  dirty = barDirty = true;
  JsonDocument d; d["t"] = "page"; d["name"] = pageName(p); sendJson(d);
  if (p == P_CAMERA) cameraEnter();
  if (p == P_DESKTOP) deskEnter();
  if (p == P_PHOTOS) photosEnter();
  if (p == P_VIEWER) viewerEnter();
  if (p == P_DEVICES) sendKV("get", "what", "devices");
  if (p == P_TESTS) sendKV("get", "what", "tests");
  if (p == P_IDEAS) sendKV("get", "what", "ideas");
  if (p == P_STATUS) sendKV("get", "what", "status");
  if (p == P_LOGS) sendKV("get", "what", "logs");
}

// ------------------------------------------------------------------ top bar
static bool battShown() {
  return battState != "none" && battRxMs && millis() - battRxMs < 60000;   // stale after a minute without news
}

// battery icon + percent at the right of the bar, left of the link dot. Returns the left edge used.
static int drawBattery(int right) {
  if (!battShown()) return right;
  int cy = BAR_H / 2, bw = 20, bh = 10, x = right - bw - 2;
  if (battState == "ext") {                       // USB power: the battery is not connected (INA219 bypassed)
    bar.setFont(&fonts::Font2); bar.setTextDatum(MR_DATUM); bar.setTextColor((uint32_t)C_DIM);
    bar.drawString("USB", right, cy);
    return right - bar.textWidth("USB");
  }
  uint32_t col = battState == "crit" ? (uint32_t)C_BAD : battState == "low" ? (uint32_t)C_WARN
               : battState == "chg" ? (uint32_t)C_BLUE : (uint32_t)C_OK;
  bar.drawRect(x, cy - bh / 2, bw, bh, (uint32_t)C_TEXT);
  bar.fillRect(x + bw, cy - 2, 2, 4, (uint32_t)C_TEXT);
  if (battState == "unk") {                       // the voltage fits none of the packs set up on the Pi
    bar.setFont(&fonts::Font0); bar.setTextDatum(MC_DATUM); bar.setTextColor((uint32_t)C_WARN);
    bar.drawString("?", x + bw / 2, cy + 1);
  } else {
    int fw = (bw - 4) * constrain(battPct, 0, 100) / 100;
    if (fw > 0) bar.fillRect(x + 2, cy - bh / 2 + 2, fw, bh - 4, col);
    if (battState == "chg") {                     // little lightning bolt
      bar.fillTriangle(x + 11, cy - 4, x + 6, cy + 1, x + 10, cy + 1, (uint32_t)C_TEXT);
      bar.fillTriangle(x + 10, cy - 1, x + 14, cy - 1, x + 9, cy + 4, (uint32_t)C_TEXT);
    }
  }
  bar.setFont(&fonts::Font2); bar.setTextDatum(MR_DATUM);
  bar.setTextColor(battState == "crit" ? (uint32_t)C_BAD : battState == "low" ? (uint32_t)C_WARN : (uint32_t)C_TEXT);
  String p = battTxt.length() ? battTxt : battState == "unk" ? String(battV, 1) + "V" : String(battPct) + "%";
  bar.drawString(p, x - 3, cy);
  return x - 3 - bar.textWidth(p);
}

static void drawBar() {
  // 0.9.2 (owner: "back is top left makes more sense to me"): top left = "< Page title" (on the face, which has no
  // title: the clock); middle = the clock (on the face: the Bluesky numbers); battery + link dot top right.
  // Tap the left or middle of the bar = back.
  barDirty = false;
  bar.fillScreen((uint32_t)C_PANEL);
  bar.setFont(&fonts::Font2);
  int cy = BAR_H / 2;
  bar.setTextColor((uint32_t)C_TEXT);
  if (page == P_FACE) {
    bar.setTextDatum(ML_DATUM);
    bar.drawString(piTime, 5, cy);
  } else {
    bar.setTextDatum(MC_DATUM);
    bar.drawString(piTime, SCR_W / 2, cy);
    bar.setTextDatum(ML_DATUM);
  }
  if (page == P_FACE) {
    // heart = likes, person = followers (the face has its own Menu button)
    String n = socOk ? String(socLikes) : String("-");
    String f = socOk ? String(socFollowers) : String("-");
    int w = 12 + 4 + bar.textWidth(n) + 10 + 11 + 4 + bar.textWidth(f);
    int x = SCR_W / 2 - w / 2;
    uint32_t heart = (uint32_t)0xF4567Bu;
    bar.fillCircle(x + 3, cy - 2, 3, heart); bar.fillCircle(x + 9, cy - 2, 3, heart);
    bar.fillTriangle(x, cy - 1, x + 12, cy - 1, x + 6, cy + 6, heart);
    bar.drawString(n, x + 16, cy);
    x += 16 + bar.textWidth(n) + 10;
    bar.fillCircle(x + 5, cy - 3, 3, (uint32_t)C_ACCENT);
    bar.fillRoundRect(x, cy + 1, 11, 6, 3, (uint32_t)C_ACCENT);
    bar.drawString(f, x + 15, cy);
  } else {
    String title = PAGE_TITLES[page];
    int x = 5;
    bar.setTextColor((uint32_t)C_ACCENT);
    bar.drawString("< ", x, cy);
    bar.setTextColor((uint32_t)C_TEXT);
    bar.drawString(title, x + bar.textWidth("< "), cy);
  }
  drawBattery(SCR_W - 16);
  bar.fillCircle(SCR_W - 8, cy, 4, linkUp() ? (uint32_t)C_OK : (uint32_t)C_BAD);
  bar.pushSprite(0, 0);
}

// ------------------------------------------------------------------ toast
static String toastMsg;
static uint32_t toastUntil = 0;
void toast(const String& s, uint32_t ms) {
  toastMsg = s; toastUntil = millis() + ms; dirty = true;
  if (page == P_CAMERA || page == P_VIEWER) drawToastLcd();
}
static bool toastOn() { return toastUntil && millis() < toastUntil; }
void drawToastOn(LGFX_Sprite& c) {
  if (!toastOn()) return;
  c.setFont(&fonts::Font2);
  int w = c.textWidth(toastMsg) + 20; if (w > CW - 10) w = CW - 10;
  int x = (CW - w) / 2, y = CH - 30;
  c.fillRoundRect(x, y, w, 24, 8, (uint32_t)C_PANEL2);
  c.drawRoundRect(x, y, w, 24, 8, (uint32_t)C_ACCENT);
  c.setTextColor((uint32_t)C_TEXT);
  c.setTextDatum(MC_DATUM);
  c.drawString(toastMsg, CW / 2, y + 12);
}
void drawToastLcd() {
  if (!toastOn()) return;
  lcd.setFont(&fonts::Font2);
  int w = lcd.textWidth(toastMsg) + 20; if (w > CW - 10) w = CW - 10;
  int x = (CW - w) / 2, y = SCR_H - 30;
  lcd.fillRoundRect(x, y, w, 24, 8, (uint32_t)C_PANEL2);
  lcd.drawRoundRect(x, y, w, 24, 8, (uint32_t)C_ACCENT);
  lcd.setTextColor((uint32_t)C_TEXT);
  lcd.setTextDatum(MC_DATUM);
  lcd.drawString(toastMsg, CW / 2, y + 12);
}

// ------------------------------------------------------------------ modal
struct Modal { bool on = false; String id, title, body; String opt[3]; uint8_t n = 0; bool local = false; } MD;
void modalAsk(const String& id, const String& title, const String& body, const char* o1, const char* o2, const char* o3, bool local) {
  MD.on = true; MD.id = id; MD.title = title; MD.body = body; MD.local = local; MD.n = 0;
  if (o1) MD.opt[MD.n++] = o1;
  if (o2) MD.opt[MD.n++] = o2;
  if (o3) MD.opt[MD.n++] = o3;
  dirty = true;
}
bool modalActive() { return MD.on; }
static void modalButtonRect(int i, int& x, int& y, int& w, int& h) {
  int n = MD.n ? MD.n : 1;
  int total = 272, gap = 8;
  w = (total - gap * (n - 1)) / n; h = 30;
  x = 24 + i * (w + gap); y = 166;
}
void modalDraw(LGFX_Sprite& c) {
  if (!MD.on) return;
  c.fillRoundRect(12, 8, 296, 204, 10, (uint32_t)C_PANEL);
  c.drawRoundRect(12, 8, 296, 204, 10, (uint32_t)C_ACCENT);
  c.setTextColor((uint32_t)C_TEXT);
  c.setFont(&fonts::FreeSansBold9pt7b);
  c.setTextDatum(TL_DATUM);
  c.drawString(MD.title, 24, 18);
  c.setFont(&fonts::Font2);
  c.setTextColor((uint32_t)C_TEXT);
  wrapText(c, MD.body, 24, 44, 272, 16, 7);
  for (int i = 0; i < MD.n; i++) {
    int x, y, w, h; modalButtonRect(i, x, y, w, h);
    button(c, x, y, w, h, MD.opt[i].c_str(), i == 0 ? C_BTN : (uint32_t)C_PANEL2);
  }
}
void modalTap(int x, int y) {
  for (int i = 0; i < MD.n; i++) {
    int bx, by, bw, bh; modalButtonRect(i, bx, by, bw, bh);
    if (x >= bx && x < bx + bw && y >= by - 4 && y < by + bh + 6) {
      MD.on = false; dirty = true;
      String choice = MD.opt[i];
      if (MD.local) onLocalAnswer(MD.id, choice);
      else if (MD.id.length()) {
        JsonDocument d; d["t"] = "answer"; d["id"] = MD.id; d["choice"] = choice; sendJson(d);
      }
      if (page == P_VIEWER || page == P_CAMERA) { lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG); }
      if (page == P_VIEWER) viewerEnter();
      return;
    }
  }
}

// ------------------------------------------------------------------ drawing helpers
static int luma(uint32_t c) { return ((c >> 16 & 0xFF) * 3 + (c >> 8 & 0xFF) * 6 + (c & 0xFF)) / 10; }
void button(LGFX_Sprite& c, int x, int y, int w, int h, const char* label, uint32_t bg, uint32_t fg) {
  if (fg == C_TEXT && abs(luma(bg) - luma(C_TEXT)) < 90)   // keep labels readable on coloured buttons in every theme
    fg = luma(bg) > 128 ? 0x101418 : 0xF5F7FA;
  c.fillRoundRect(x, y, w, h, 7, bg);
  c.drawRoundRect(x, y, w, h, 7, (uint32_t)C_LINE);
  c.setFont(&fonts::Font2);
  c.setTextColor(fg);
  c.setTextDatum(MC_DATUM);
  c.drawString(label, x + w / 2, y + h / 2);
}
void wrapText(LGFX_Sprite& c, const String& s, int x, int y, int w, int lineH, int maxLines) {
  c.setTextDatum(TL_DATUM);
  int line = 0, start = 0, n = s.length();
  while (start < n && line < maxLines) {
    int nl = s.indexOf('\n', start); if (nl < 0) nl = n;
    String para = s.substring(start, nl);
    int p = 0;
    while (p <= (int)para.length() && line < maxLines) {
      int best = -1, i = p;
      while (true) {
        int sp = para.indexOf(' ', i); int end = sp < 0 ? para.length() : sp;
        if (c.textWidth(para.substring(p, end)) > w) break;
        best = end; if (sp < 0) break; i = sp + 1;
      }
      if (best < 0) {  // single long word: hard cut
        best = p; while (best < (int)para.length() && c.textWidth(para.substring(p, best + 1)) <= w) best++;
        if (best == p) best = p + 1;
      }
      c.drawString(para.substring(p, best), x, y + line * lineH);
      line++; p = best; while (p < (int)para.length() && para[p] == ' ') p++;
      if (p >= (int)para.length()) break;
    }
    start = nl + 1;
  }
}
uint32_t statusColor(const char* st) {
  if (!st) return C_GRAY;
  if (!strcmp(st, "ok") || !strcmp(st, "pass")) return C_OK;
  if (!strcmp(st, "warn") || !strcmp(st, "flaky")) return C_WARN;
  if (!strcmp(st, "missing") || !strcmp(st, "fail") || !strcmp(st, "bad")) return C_BAD;
  if (!strcmp(st, "planned") || !strcmp(st, "none")) return C_GRAY;
  return C_BLUE;
}
void statusDot(LGFX_Sprite& c, int x, int y, const char* st) { c.fillCircle(x, y, 5, statusColor(st)); }

void pushCanvas() {
  drawToastOn(canvas);
  modalDraw(canvas);
  canvas.pushSprite(0, BAR_H);
}

// ------------------------------------------------------------------ sound, LED, light
void playTone(uint16_t hz, uint16_t ms) {
  if (S.vol == 0 || hz < 130) return;
  audioStop();  // the cosine generator and the PCM player share DAC channel 1
  dac_cosine_handle_t h;
  dac_cosine_config_t cfg = {};
  cfg.chan_id = DAC_CHAN_1;  // GPIO26 -> speaker amplifier
  cfg.freq_hz = hz;
  cfg.clk_src = DAC_COSINE_CLK_SRC_DEFAULT;
  cfg.atten = S.vol == 1 ? DAC_COSINE_ATTEN_DB_18 : (S.vol == 2 ? DAC_COSINE_ATTEN_DB_12 : DAC_COSINE_ATTEN_DB_6);
  cfg.phase = DAC_COSINE_PHASE_0;
  cfg.offset = 0;
  cfg.flags.force_set_freq = true;
  if (dac_cosine_new_channel(&cfg, &h) != ESP_OK) return;
  dac_cosine_start(h);
  delay(ms);
  dac_cosine_stop(h);
  dac_cosine_del_channel(h);
}
void playChime(uint8_t kind) {
  switch (kind) {
    case 0: playTone(880, 70); playTone(1175, 70); playTone(1568, 110); break;  // happy
    case 1: playTone(1319, 90); playTone(988, 120); break;                      // attention
    case 2: playTone(392, 160); playTone(330, 220); break;                      // sad / error
    default: playTone(1000, 100); break;
  }
}
void setLed(uint8_t r, uint8_t g, uint8_t b) {  // LEDs are active low
  ledcWrite(PIN_LED_R, 255 - r);
  ledcWrite(PIN_LED_G, 255 - g);
  ledcWrite(PIN_LED_B, 255 - b);
}
int readLight() { return analogRead(PIN_LDR); }

static uint32_t ledOverrideUntil = 0;
static void ledUpdate() {
  static uint32_t last = 0;
  if (millis() - last < 100 || millis() < ledOverrideUntil) return;
  last = millis();
  if (!linkUp()) {
    bool on = (millis() / 500) % 2;
    setLed(on ? 40 : 0, 0, 0);
  } else {
    uint32_t c = moodColor(curMood);
    setLed(((c >> 16) & 0xFF) / 16, ((c >> 8) & 0xFF) / 16, (c & 0xFF) / 16);
  }
}
static void autoBrightness() {
  static uint32_t last = 0; static int lo = 4095, hi = 0;
  if (!S.autoBright || screenAsleep || millis() - last < 1000) return;
  last = millis();
  int v = readLight(); if (v < lo) lo = v; if (v > hi) hi = v;
  if (hi - lo < 150) return;
  float dark = (float)(v - lo) / (hi - lo);   // CYD LDR reads higher in the dark
  lcd.setBrightness(255 - (int)(dark * 200));
}

// ------------------------------------------------------------------ touch
struct TouchState { bool down = false, drag = false, ignore = false, held = false; int x0 = 0, y0 = 0, x = 0, y = 0, lastY = 0; uint32_t t0 = 0, lastRep = 0; } TS;
static void tapDispatch(int x, int y) {
  if (imageOverlay) { imageOverlay = false; dirty = true; if (page == P_VIEWER || page == P_CAMERA) lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG); if (page == P_VIEWER) viewerEnter(); return; }
  if (MD.on) { modalTap(x, y - BAR_H); return; }
  if (y < BAR_H) {
    if (x >= SCR_W - 90 && battShown()) {        // the battery: Settings (Battery is the first group) + the numbers
      sendEvt("batt_tap");
      if (page != P_SETTINGS) goPage(P_SETTINGS);
      return;
    }
    if (page == P_FACE) {                        // the Bluesky numbers in the middle: details
      if (x >= 60) {
        toast(socOk ? String("Bluesky: ") + socPosts + " posts, " + socLikes + " likes, " + socFollowers + " followers"
                    : String("Bluesky numbers not loaded yet"), 3500);
        sendKV("get", "what", "social");
      }
      return;
    }
    goPage(backTarget(page));                    // "< Title" or the clock: back
    return;
  }
  int cy = y - BAR_H;
  if (scrollBarHit(x, cy)) { scrollBarTap(x, cy); return; }
  switch (page) {
    case P_FACE: faceTap(x, cy); break;
    case P_MENU: menuTap(x, cy); break;
    case P_DEVICES: devicesTap(x, cy); break;
    case P_TESTS: testsTap(x, cy); break;
    case P_CAMERA: cameraTap(x, cy); break;
    case P_PHOTOS: photosTap(x, cy); break;
    case P_VIEWER: viewerTap(x, cy); break;
    case P_IMU: imuTap(x, cy); break;
    case P_LOGS: logsTap(x, cy); break;
    case P_IDEAS: ideasTap(x, cy); break;
    case P_SETTINGS: settingsTap(x, cy); break;
    case P_ABOUT: aboutTap(x, cy); break;
    case P_STATUS: sendKV("get", "what", "status"); break;
    default: break;
  }
}
static void dragDispatch(int dy) {
  if (MD.on) return;
  if (scrollBarHit(TS.x0, TS.y0 - BAR_H)) { scrollBarDrag(dy); return; }
  switch (page) {
    case P_DEVICES: devicesDrag(dy); break;
    case P_TESTS: testsDrag(dy); break;
    case P_PHOTOS: photosDrag(dy); break;
    case P_LOGS: logsDrag(dy); break;
    case P_IDEAS: ideasDrag(dy); break;
    case P_SETTINGS: settingsDrag(dy); break;
    default: break;
  }
}
static void pollTouch() {
  static uint32_t last = 0;
  if (millis() - last < 15) return;
  last = millis();
  int32_t x, y;
  bool d = lcd.getTouch(&x, &y);
  if (d) {
    if (!TS.down) {
      TS = TouchState(); TS.down = true; TS.x0 = TS.x = x; TS.y0 = TS.y = TS.lastY = y; TS.t0 = millis();
      if (screenAsleep) { screenAsleep = false; applyDisplaySettings(); dirty = barDirty = true; TS.ignore = true; sendEvt("wake"); return; }
      if (page == P_TOUCHTEST && y >= BAR_H) touchTestTouch(x, y - BAR_H, true);
      if (page == P_DESKTOP && y >= BAR_H && !MD.on && !imageOverlay) deskTouch('d', x, (int)(y - BAR_H));
    } else {
      TS.x = x; TS.y = y;
      if (TS.ignore) return;
      if (page == P_TOUCHTEST && y >= BAR_H) { touchTestTouch(x, y - BAR_H, true); return; }
      if (page == P_DESKTOP && TS.y0 >= BAR_H && !MD.on && !imageOverlay) { deskTouch('m', x, max(0, (int)(y - BAR_H))); return; }
      bool onBar = scrollBarHit(TS.x0, TS.y0 - BAR_H);
      if (onBar && !TS.drag && millis() - TS.t0 > 380 && millis() - TS.lastRep > 90 && scrollArrowZone(TS.x0, TS.y0 - BAR_H)) {
        scrollBarTap(TS.x0, TS.y0 - BAR_H);     // press and hold an arrow: keeps scrolling
        TS.lastRep = millis(); TS.held = true;
      }
      if (!TS.drag && !TS.held && abs(y - TS.y0) > (onBar ? 4 : 14)) TS.drag = true;
      if (TS.drag) { int dy = y - TS.lastY; if (dy) { dragDispatch(dy); TS.lastY = y; } }
    }
  } else if (TS.down) {
    TS.down = false;
    if (TS.ignore) return;
    if (page == P_TOUCHTEST && TS.y0 >= BAR_H) { touchTestTouch(TS.x, TS.y - BAR_H, false); return; }
    if (page == P_DESKTOP && TS.y0 >= BAR_H && !MD.on && !imageOverlay) { deskTouch('u', TS.x, max(0, (int)(TS.y - BAR_H))); return; }
    if (!TS.drag && !TS.held) tapDispatch(TS.x0, TS.y0);
  }
}

// ------------------------------------------------------------------ render
static void renderPage() {
  if (screenAsleep || imageOverlay) return;
  if (MD.on && (page == P_CAMERA || page == P_VIEWER || page == P_DESKTOP)) {
    if (!canvasOk) return;
    canvas.fillScreen((uint32_t)C_BG);
    pushCanvas();
    return;
  }
  if (page == P_CAMERA) { cameraDraw(); return; }
  if (page == P_DESKTOP) { deskDraw(); return; }
  if (page == P_VIEWER) { viewerDraw(); return; }
  if (!canvasOk) return;
  switch (page) {
    case P_FACE: faceDraw(); break;
    case P_MENU: menuDraw(); break;
    case P_STATUS: statusDraw(); break;
    case P_DEVICES: devicesDraw(); break;
    case P_TESTS: testsDraw(); break;
    case P_PHOTOS: photosDraw(); break;
    case P_IMU: imuDraw(); break;
    case P_LOGS: logsDraw(); break;
    case P_IDEAS: ideasDraw(); break;
    case P_SETTINGS: settingsDraw(); break;
    case P_ABOUT: aboutDraw(); break;
    case P_TOUCHTEST: touchTestDraw(); break;
    default: canvas.fillScreen((uint32_t)C_BG); break;
  }
  pushCanvas();
  if (page == P_PHOTOS && !MD.on) photosPreviewRedraw();
}

// ------------------------------------------------------------------ panel detection
static void detectPanel() {
  uint32_t id04 = lcd.getPanel()->readCommand(0x04, 1, 3);
  uint32_t idD3 = lcd.getPanel()->readCommand(0xD3, 1, 3);
  char b[48];
  snprintf(b, sizeof(b), "%s 04=%06lX D3=%06lX", S.st7789 ? "ST7789" : "ILI9341", (unsigned long)(id04 & 0xFFFFFF), (unsigned long)(idD3 & 0xFFFFFF));
  panelInfo = b;
  prefs.begin("cyd", true);
  bool userSet = prefs.getBool("panelset", false);
  prefs.end();
  // ST7789 answers RDDID with 85 85 52; switch automatically once if the user never chose a panel.
  bool looksST = ((id04 & 0xFF) == 0x85 || ((id04 >> 16) & 0xFF) == 0x85) && !S.st7789;
  if (looksST && !userSet) {
    S.st7789 = true; saveSettings();
    prefs.begin("cyd", false); prefs.putBool("panelset", true); prefs.end();
    delay(20); ESP.restart();
  }
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.setRxBufferSize(16384);
  Serial.begin(921600);
  bootMs = millis();
  crcInit();
  loadSettings();
  applyTheme(S.theme);
  canvas.setColorDepth(8);
  canvasOk = canvas.createSprite(CW, CH) != nullptr;  // allocate the big buffer first
  allocBuffers();
  lcd.usePanel(S.st7789);
  lcd.init();
  applyDisplaySettings();
  lcd.fillScreen((uint32_t)C_BG);
  detectPanel();
  bar.setColorDepth(16);
  bar.createSprite(SCR_W, BAR_H);
  ledcAttach(PIN_LED_R, 12000, 8);
  ledcAttach(PIN_LED_G, 12000, 8);
  ledcAttach(PIN_LED_B, 12000, 8);
  setLed(0, 0, 0);
  analogReadResolution(12);
  pinMode(PIN_LDR, INPUT);

  sensorsInit();
  if (!S.calOk && !S.affOk) runCalibration();
  faceInit();
  faceSetMood(M_BOOTING, 2500);
  sendHello();
  playChime(0);
}

void loop() {
  serialPoll();
  pollTouch();
  sensorsPoll();
  audioPoll();
  uint32_t now = millis();

  static uint32_t lastHb = 0, lastHello = 0, lastFrame = 0, lastBar = 0;
  if (now - lastHb > 2000) { lastHb = now; sendHeartbeat(); }
  if (!helloAcked && now - lastHello > 3000) { lastHello = now; sendHello(); }

  if (page == P_FACE && now - lastFrame >= 40) dirty = true;
  if (toastUntil && now > toastUntil) { toastUntil = 0; dirty = true; }
  if (dirty && !binMode && (page != P_FACE || now - lastFrame >= 33)) {
    dirty = false; lastFrame = now;
    renderPage();
  }
  if (barDirty || now - lastBar > 1000) { lastBar = now; if (!screenAsleep) drawBar(); }
  ledUpdate();
  autoBrightness();
}
