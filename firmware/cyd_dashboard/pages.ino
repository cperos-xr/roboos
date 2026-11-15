// Menu, status, devices, tests, logs, ideas, settings, about and touch-test pages.
#include "common.h"
#include <Preferences.h>

// ============================================================ MENU
// 5 columns x 3 rows. The right column opens RoboOS (drawn by the Pi): app = the RoboOS app to open first.
struct Tile { const char* label; PageId page; uint8_t icon; const char* app; };
#define MENU_COLS 5
#define MENU_N 15
static const Tile TILES[MENU_N] = {
  {"Face", P_FACE, 0, nullptr}, {"Status", P_STATUS, 1, nullptr}, {"Devices", P_DEVICES, 2, nullptr}, {"Tests", P_TESTS, 3, nullptr}, {"Games", P_DESKTOP, 12, "games"},
  {"Camera", P_CAMERA, 4, nullptr}, {"Photos", P_PHOTOS, 5, nullptr}, {"IMU", P_IMU, 6, nullptr}, {"Logs", P_LOGS, 7, nullptr}, {"RoboOS", P_DESKTOP, 13, nullptr},
  {"Ideas", P_IDEAS, 8, nullptr}, {"Settings", P_SETTINGS, 9, nullptr}, {"About", P_ABOUT, 10, nullptr}, {"Sleep", P_COUNT, 11, nullptr}, {"Bluetooth", P_DESKTOP, 14, "bluetooth"},
};

static void icon(LGFX_Sprite& c, int cx, int cy, uint8_t k, uint32_t col) {
  switch (k) {
    case 0: c.fillRoundRect(cx - 14, cy - 8, 10, 14, 3, col); c.fillRoundRect(cx + 4, cy - 8, 10, 14, 3, col);
            c.fillArc(cx, cy + 2, 10, 13, 40, 140, col); break;
    case 1: for (int i = 0; i < 4; i++) { int h = 6 + ((i * 7) % 4) * 5 + i * 2; c.fillRect(cx - 14 + i * 8, cy + 10 - h, 6, h, col); } break;
    case 2: c.drawRect(cx - 10, cy - 10, 20, 20, col); c.fillRect(cx - 5, cy - 5, 10, 10, col);
            for (int i = -6; i <= 6; i += 6) { c.drawFastHLine(cx - 15, cy + i, 4, col); c.drawFastHLine(cx + 11, cy + i, 4, col);
                                               c.drawFastVLine(cx + i, cy - 15, 4, col); c.drawFastVLine(cx + i, cy + 11, 4, col); } break;
    case 3: c.drawWideLine(cx - 12, cy, cx - 4, cy + 9, 2.5f, col); c.drawWideLine(cx - 4, cy + 9, cx + 13, cy - 10, 2.5f, col); break;
    case 4: c.fillRoundRect(cx - 15, cy - 8, 30, 20, 4, col); c.fillRect(cx - 6, cy - 12, 12, 5, col);
            c.fillCircle(cx, cy + 2, 6, (uint32_t)C_PANEL); c.fillCircle(cx, cy + 2, 3, col); break;
    case 5: c.drawRect(cx - 15, cy - 11, 30, 22, col); c.fillTriangle(cx - 12, cy + 8, cx - 3, cy - 3, cx + 5, cy + 8, col);
            c.fillTriangle(cx, cy + 8, cx + 6, cy, cx + 12, cy + 8, col); c.fillCircle(cx + 8, cy - 5, 3, col); break;
    case 6: c.drawCircle(cx, cy, 13, col); c.drawWideLine(cx - 13, cy + 3, cx + 13, cy - 3, 1.5f, col);
            c.fillTriangle(cx - 4, cy + 1, cx + 4, cy + 1, cx, cy - 5, col); break;
    case 7: for (int i = 0; i < 4; i++) c.fillRect(cx - 14, cy - 10 + i * 6, i % 2 ? 20 : 28, 3, col); break;
    case 8: c.fillCircle(cx, cy - 3, 9, col); c.fillRect(cx - 4, cy + 6, 8, 6, col); c.drawFastHLine(cx - 4, cy + 13, 8, col); break;
    case 9: c.fillCircle(cx, cy, 10, col); c.fillCircle(cx, cy, 4, (uint32_t)C_PANEL);
            for (int a = 0; a < 360; a += 45) { float r = a * DEG_TO_RAD; c.fillCircle(cx + cosf(r) * 11, cy + sinf(r) * 11, 3, col); } break;
    case 10: c.drawCircle(cx, cy, 12, col); c.fillRect(cx - 2, cy - 2, 4, 9, col); c.fillCircle(cx, cy - 7, 2, col); break;
    case 11: c.fillCircle(cx, cy, 11, col); c.fillCircle(cx + 6, cy - 5, 10, (uint32_t)C_PANEL); break;
    case 12:  // game pad
      c.fillRoundRect(cx - 15, cy - 8, 30, 17, 8, col);
      c.fillRect(cx - 10, cy - 1, 8, 2, (uint32_t)C_PANEL); c.fillRect(cx - 7, cy - 4, 2, 8, (uint32_t)C_PANEL);
      c.fillCircle(cx + 6, cy - 2, 2, (uint32_t)C_PANEL); c.fillCircle(cx + 10, cy + 2, 2, (uint32_t)C_PANEL); break;
    case 13:  // a little window
      c.drawRect(cx - 14, cy - 11, 28, 22, col); c.fillRect(cx - 14, cy - 11, 28, 6, col);
      c.fillRect(cx - 10, cy - 1, 6, 6, col); c.fillRect(cx - 1, cy - 1, 10, 2, col); c.fillRect(cx - 1, cy + 3, 7, 2, col); break;
    case 14:  // bluetooth rune
      c.drawWideLine(cx - 1, cy - 12, cx - 1, cy + 12, 2.0f, col);
      c.drawWideLine(cx - 1, cy - 12, cx + 7, cy - 5, 2.0f, col); c.drawWideLine(cx + 7, cy - 5, cx - 8, cy + 6, 2.0f, col);
      c.drawWideLine(cx - 1, cy + 12, cx + 7, cy + 5, 2.0f, col); c.drawWideLine(cx + 7, cy + 5, cx - 8, cy - 6, 2.0f, col); break;
  }
}

void menuDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  for (int i = 0; i < MENU_N; i++) {
    int col = i % MENU_COLS, row = i / MENU_COLS;
    int x = 3 + col * 63, y = 4 + row * 72, w = 60, h = 68;
    canvas.fillRoundRect(x, y, w, h, 9, (uint32_t)C_PANEL);
    icon(canvas, x + w / 2, y + 26, TILES[i].icon, i == 0 ? moodColor(curMood) : (uint32_t)C_ACCENT);
    canvas.setFont(&fonts::Font2);
    if (canvas.textWidth(TILES[i].label) > w - 2) canvas.setFont(&fonts::Font0);
    canvas.setTextColor((uint32_t)C_TEXT);
    canvas.setTextDatum(MC_DATUM);
    canvas.drawString(TILES[i].label, x + w / 2, y + 54);
  }
}
void menuTap(int x, int y) {
  int col = (x - 3) / 63, row = (y - 4) / 72;
  if (col < 0 || col >= MENU_COLS || row < 0 || row > 2) return;
  const Tile& t = TILES[row * MENU_COLS + col];
  if (t.app) sendKV("open_app", "name", t.app);
  if (t.page == P_COUNT) {  // sleep
    screenAsleep = true; lcd.setBrightness(0); sendEvt("sleep"); goPage(P_FACE);
    return;
  }
  goPage(t.page);
}

// ============================================================ STATUS
static void meter(int x, int y, int w, const char* label, float frac, const String& val, uint32_t col) {
  canvas.setFont(&fonts::Font2);
  canvas.setTextColor((uint32_t)C_DIM);
  canvas.setTextDatum(TL_DATUM);
  canvas.drawString(label, x, y);
  canvas.setTextColor((uint32_t)C_TEXT);
  canvas.setTextDatum(TR_DATUM);
  canvas.drawString(val, x + w, y);
  canvas.fillRoundRect(x, y + 17, w, 6, 3, (uint32_t)C_PANEL2);
  frac = constrain(frac, 0.0f, 1.0f);
  if (frac > 0) canvas.fillRoundRect(x, y + 17, max(6, (int)(w * frac)), 6, 3, col);
}
static String fmtUp(uint32_t s) {
  char b[24];
  if (s >= 86400) snprintf(b, sizeof(b), "%lud %luh", (unsigned long)(s / 86400), (unsigned long)((s % 86400) / 3600));
  else if (s >= 3600) snprintf(b, sizeof(b), "%luh %lum", (unsigned long)(s / 3600), (unsigned long)((s % 3600) / 60));
  else snprintf(b, sizeof(b), "%lum %lus", (unsigned long)(s / 60), (unsigned long)(s % 60));
  return b;
}
void statusDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonDocument& s = statusDoc;
  if (s["t"].isNull()) {
    canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(MC_DATUM);
    canvas.drawString(linkUp() ? "waiting for status..." : "no link to Orange Pi", CW / 2, CH / 2);
    return;
  }
  float cpu = s["cpu"] | 0.0f, temp = s["temp"] | 0.0f;
  float mu = s["mem_used"] | 0.0f, mt = s["mem_total"] | 1.0f, du = s["disk_used"] | 0.0f, dt = s["disk_total"] | 1.0f;
  char b[48];
  snprintf(b, sizeof(b), "%.0f%%", cpu); meter(6, 4, 148, "CPU", cpu / 100, b, cpu > 85 ? C_WARN : C_OK);
  snprintf(b, sizeof(b), "%.1f C", temp); meter(166, 4, 148, "Temp", (temp - 20) / 60, b, temp > 70 ? C_BAD : (temp > 60 ? C_WARN : C_OK));
  snprintf(b, sizeof(b), "%.0f/%.0f MB", mu, mt); meter(6, 32, 148, "RAM", mu / mt, b, mu / mt > 0.85f ? C_WARN : C_ACCENT);
  snprintf(b, sizeof(b), "%.1f/%.1f GB", du, dt); meter(166, 32, 148, "Disk", du / dt, b, du / dt > 0.9f ? C_WARN : C_ACCENT);

  canvas.setFont(&fonts::Font2);
  canvas.setTextDatum(TL_DATUM);
  int y = 62;
  auto row = [&](const char* k, const String& v, uint32_t vc) {
    canvas.setTextColor((uint32_t)C_DIM); canvas.drawString(k, 6, y);
    canvas.setTextColor(vc); canvas.drawString(v, 78, y); y += 16;
  };
  JsonArray ld = s["load"].as<JsonArray>();
  snprintf(b, sizeof(b), "%.2f %.2f %.2f", ld[0] | 0.0f, ld[1] | 0.0f, ld[2] | 0.0f);
  row("Load", b, C_TEXT);
  row("Uptime", fmtUp(s["up"] | 0) + "   robotd " + fmtUp(s["robotd_up"] | 0), C_TEXT);
  String ips;
  for (JsonPair kv : s["ips"].as<JsonObject>()) { if (ips.length()) ips += "  "; ips += kv.key().c_str(); ips += " "; ips += kv.value().as<const char*>(); }
  row("Network", ips.length() ? ips : String("none"), C_TEXT);
  row("Internet", (s["net"] | false) ? "online" : "offline", (s["net"] | false) ? C_OK : C_WARN);
  float ie = s["i2c_err"] | -1.0f;
  if (ie >= 0) { snprintf(b, sizeof(b), "%.1f%% errors (retries hide them)", ie); row("I2C bus", b, ie > 5 ? C_BAD : (ie > 0.5f ? C_WARN : C_OK)); }
  if (s["rp2040"].is<const char*>()) row("RP2040", s["rp2040"].as<const char*>(), C_TEXT);
  if (s["battery"].is<const char*>()) row("Power", s["battery"].as<const char*>(), C_TEXT);
  if (s["ai"].is<const char*>()) row("AI", s["ai"].as<const char*>(), C_TEXT);

  canvas.drawFastHLine(6, y + 2, CW - 12, (uint32_t)C_LINE); y += 6;
  snprintf(b, sizeof(b), "fw %s  heap %uk  up %s", FW_VER, (unsigned)(ESP.getFreeHeap() / 1024), fmtUp(millis() / 1000).c_str());
  row("Screen", b, C_TEXT);
  snprintf(b, sizeof(b), "rx %lu  err %lu  light %d", (unsigned long)rxCount, (unsigned long)rxErr, readLight());
  row("Link", b, linkUp() ? C_TEXT : C_BAD);
}

// ============================================================ generic scroll list
static void clampScroll(ListView& lv, int n, int top) {
  int maxS = max(0, n * lv.rowH - (CH - top));
  lv.scroll = constrain(lv.scroll, 0, maxS);
}
// ---- classic scroll bar -------------------------------------------------------------------
static ScrollCtl scrollCtl();   // geometry of the current page's list (defined at the end of this file)
static int sbAccum = 0;
// classic bevelled shapes, coloured from the active theme
static void bevel(LGFX_Sprite& c, int x, int y, int w, int h, uint32_t face, uint32_t hi, uint32_t lo) {
  c.fillRect(x, y, w, h, face);
  c.drawFastHLine(x, y, w, hi); c.drawFastVLine(x, y, h, hi);
  c.drawFastHLine(x, y + h - 1, w, lo); c.drawFastVLine(x + w - 1, y, h, lo);
  c.drawFastHLine(x + 1, y + h - 2, w - 2, C_LINE); c.drawFastVLine(x + w - 2, y + 1, h - 2, C_LINE);
}
static int sbTopPos(const ScrollCtl& s) {
  int maxS = max(0, s.total - s.view), p = constrain(*s.pos, 0, maxS);
  return s.fromBottom ? maxS - p : p;
}
static void sbSetTop(const ScrollCtl& s, int top) {
  int maxS = max(0, s.total - s.view);
  top = constrain(top, 0, maxS);
  *s.pos = s.fromBottom ? maxS - top : top;
  dirty = true;
}
static void sbThumb(const ScrollCtl& s, int& ty, int& th) {
  int track = s.h - 2 * SB_W;
  if (s.total <= s.view) { ty = s.top + SB_W; th = track; return; }
  th = max(20, track * s.view / s.total);
  ty = s.top + SB_W + (track - th) * sbTopPos(s) / max(1, s.total - s.view);
}
static void sbArrow(LGFX_Sprite& c, int x, int y, bool up, bool en) {
  bevel(c, x, y, SB_W, SB_W, C_PANEL2, C_LINE, C_BG);
  int cx = x + SB_W / 2 - 1, cy = y + SB_W / 2 - 1;
  uint32_t col = en ? C_ACCENT : C_GRAY;
  if (up) c.fillTriangle(cx, cy - 4, cx - 6, cy + 3, cx + 6, cy + 3, col);
  else c.fillTriangle(cx, cy + 4, cx - 6, cy - 3, cx + 6, cy - 3, col);
}
static void drawScrollBar(LGFX_Sprite& c, const ScrollCtl& s) {
  if (!s.pos) return;
  bool en = s.total > s.view;
  for (int yy = s.top + SB_W; yy < s.top + s.h - SB_W; yy++)        // the classic checkered track
    for (int xx = s.x; xx < s.x + SB_W; xx++) c.drawPixel(xx, yy, ((xx + yy) & 1) ? C_PANEL : C_BG);
  sbArrow(c, s.x, s.top, true, en);
  sbArrow(c, s.x, s.top + s.h - SB_W, false, en);
  if (en) {
    int ty, th; sbThumb(s, ty, th);
    bevel(c, s.x, ty, SB_W, th, C_BTN, C_ACCENT, C_BG);
    for (int g = -4; g <= 4; g += 4) {                                // grip lines
      c.drawFastHLine(s.x + 6, ty + th / 2 + g, SB_W - 12, C_BG);
      c.drawFastHLine(s.x + 6, ty + th / 2 + g + 1, SB_W - 12, C_ACCENT);
    }
  }
}
bool scrollBarHit(int x, int y) {
  ScrollCtl s = scrollCtl();
  return s.pos && x >= s.x - 4 && x < s.x + SB_W + 4 && y >= s.top && y < s.top + s.h;   // a few px of slack: easier to hit
}
bool scrollArrowZone(int x, int y) {
  ScrollCtl s = scrollCtl();
  return scrollBarHit(x, y) && (y < s.top + SB_W || y >= s.top + s.h - SB_W);
}
void scrollBarTap(int x, int y) {
  ScrollCtl s = scrollCtl();
  if (!s.pos) return;
  int top = sbTopPos(s);
  if (y < s.top + SB_W) sbSetTop(s, top - s.step);
  else if (y >= s.top + s.h - SB_W) sbSetTop(s, top + s.step);
  else {
    int ty, th; sbThumb(s, ty, th);
    if (y < ty) sbSetTop(s, top - max(s.step, s.view - s.step));
    else if (y >= ty + th) sbSetTop(s, top + max(s.step, s.view - s.step));
  }
}
void scrollBarDrag(int dy) {     // dragging the thumb: list moves the same way as the finger
  ScrollCtl s = scrollCtl();
  if (!s.pos || s.total <= s.view) return;
  int ty, th; sbThumb(s, ty, th);
  int track = s.h - 2 * SB_W - th;
  if (track <= 0) return;
  sbAccum += dy * (s.total - s.view);
  int d = sbAccum / track;
  sbAccum -= d * track;
  if (d) sbSetTop(s, sbTopPos(s) + d);
}

// ============================================================ DEVICES (wiring map + list)
static ListView devLV;
static const int MAP_H = 120;
static const MapNode NODES[] = {
  {"power", "Power", 26, 16}, {"pi", "Orange Pi", 92, 16}, {"hub", "USB hub", 160, 16}, {"cam", "Webcam", 234, 16},
  {"cyd", "Screen", 160, 54}, {"imu", "IMU", 220, 46}, {"pcf", "PCF8574", 220, 63}, {"spk", "Speaker", 268, 54},
  {"rp", "RP2040", 92, 92}, {"l293", "L293D", 160, 92}, {"motors", "Motors", 234, 92}, {"pispk", "Pi spkr", 30, 54},
};
static const MapEdge EDGES[] = {
  {"power", "pi", "5V"}, {"pi", "hub", "USB"}, {"hub", "cam", ""}, {"hub", "cyd", ""}, {"cyd", "imu", "I2C"}, {"cyd", "pcf", ""},
  {"cyd", "spk", ""}, {"pi", "rp", "13-pin"}, {"rp", "l293", ""}, {"l293", "motors", ""}, {"pi", "pispk", ""},
};
static const MapNode* findNode(const char* k) {
  for (auto& n : NODES) if (!strcmp(n.k, k)) return &n;
  return nullptr;
}
static const char* nodeStatus(const char* k) {
  for (JsonObject it : devDoc["items"].as<JsonArray>()) if (!strcmp(it["k"] | "", k)) return it["s"] | "unknown";
  return "unknown";
}
static void drawDeviceMap(int y0) {
  canvas.setFont(&fonts::Font0);
  for (auto& e : EDGES) {                       // wires first, boxes on top
    const MapNode *a = findNode(e.a), *b = findNode(e.b);
    if (!a || !b) continue;
    bool live = strcmp(nodeStatus(e.b), "planned") && strcmp(nodeStatus(e.b), "missing");
    uint32_t col = live ? C_ACCENT : C_GRAY;
    if (a->y == b->y || a->x == b->x) canvas.drawLine(a->x, y0 + a->y, b->x, y0 + b->y, col);
    else {                                       // right-angle wire: down/up then across
      canvas.drawFastVLine(a->x, y0 + min(a->y, b->y), abs(b->y - a->y), col);
      canvas.drawFastHLine(min(a->x, b->x), y0 + b->y, abs(b->x - a->x), col);
    }
    if (*e.label) {
      canvas.setTextColor(C_DIM); canvas.setTextDatum(MC_DATUM);
      int lx = (a->y == b->y) ? (a->x + b->x) / 2 : a->x + 18, ly = (a->y == b->y) ? a->y - 9 : (a->y + b->y) / 2;
      canvas.drawString(e.label, lx, y0 + ly);
    }
  }
  for (auto& n : NODES) {
    const char* st = nodeStatus(n.k);
    int w = strlen(n.label) * 6 + 10, h = 14;
    int x = n.x - w / 2, y = y0 + n.y - h / 2;
    canvas.fillRoundRect(x, y, w, h, 4, C_PANEL2);
    canvas.drawRoundRect(x, y, w, h, 4, statusColor(st));
    canvas.setTextColor(strcmp(st, "planned") ? C_TEXT : C_DIM); canvas.setTextDatum(MC_DATUM);
    canvas.drawString(n.label, n.x, y + h / 2);
  }
  // legend
  int lx = 6, ly = y0 + 110;
  const char* leg[][2] = {{"ok", "ok"}, {"warn", "warn"}, {"missing", "missing"}, {"planned", "planned"}, {"unknown", "untested"}};
  canvas.setTextDatum(ML_DATUM);
  for (auto& l : leg) {
    canvas.fillCircle(lx + 3, ly, 3, statusColor(l[0]));
    canvas.setTextColor(C_DIM); canvas.drawString(l[1], lx + 9, ly);
    lx += 9 + strlen(l[1]) * 6 + 10;
  }
  canvas.drawFastHLine(0, y0 + MAP_H - 2, CW - SB_W - 2, C_LINE);
}
void devicesDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonArray items = devDoc["items"].as<JsonArray>();
  int n = items.isNull() ? 0 : items.size();
  int maxS = max(0, MAP_H + n * devLV.rowH - CH);
  devLV.scroll = constrain(devLV.scroll, 0, maxS);
  drawDeviceMap(-devLV.scroll);
  if (n == 0) {
    canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(MC_DATUM);
    canvas.drawString(linkUp() ? "scanning devices..." : "no link to Orange Pi", CW / 2, MAP_H + 30 - devLV.scroll);
  }
  int i = 0;
  for (JsonObject it : items) {
    int y = MAP_H + i * devLV.rowH - devLV.scroll; i++;
    if (y < -devLV.rowH || y > CH) continue;
    canvas.fillRect(0, y, CW - SB_W - 2, devLV.rowH - 2, (uint32_t)C_PANEL);
    statusDot(canvas, 12, y + 14, it["s"] | "unknown");
    canvas.setFont(&fonts::Font2); canvas.setTextDatum(TL_DATUM);
    canvas.setTextColor((uint32_t)C_TEXT); canvas.drawString(it["n"] | "?", 24, y + 1);
    canvas.setFont(&fonts::Font0); canvas.setTextColor((uint32_t)C_DIM);
    canvas.drawString(it["d"] | "", 24, y + 18);
    canvas.setTextDatum(TR_DATUM); canvas.setTextColor(statusColor(it["s"] | "unknown"));
    canvas.drawString(it["s"] | "", CW - SB_W - 8, y + 4);
  }
  drawScrollBar(canvas, scrollCtl());
}
void devicesTap(int x, int y) {
  JsonArray items = devDoc["items"].as<JsonArray>();
  int yy = y + devLV.scroll;
  if (yy < MAP_H) {                              // tap a box on the map: show that device
    for (auto& nd : NODES) {
      int w = strlen(nd.label) * 6 + 10;
      if (abs(x - nd.x) <= w / 2 + 3 && abs(yy - nd.y) <= 10) {
        for (JsonObject it : items) if (!strcmp(it["k"] | "", nd.k)) {
          String body = String((const char*)(it["d"] | "")) + "\nstatus: " + (const char*)(it["s"] | "?");
          if (it["info"].is<const char*>()) body += String("\n") + (const char*)it["info"];
          modalAsk("", String((const char*)(it["n"] | "Device")), body, "OK", nullptr, nullptr, true);
          return;
        }
      }
    }
    return;
  }
  int i = (yy - MAP_H) / devLV.rowH;
  if (items.isNull() || i < 0 || i >= (int)items.size()) return;
  JsonObject it = items[i];
  String body = String((const char*)(it["d"] | "")) + "\nstatus: " + (const char*)(it["s"] | "?");
  if (it["info"].is<const char*>()) body += String("\n") + (const char*)it["info"];
  modalAsk("", String((const char*)(it["n"] | "Device")), body, "OK", nullptr, nullptr, true);
}
void devicesDrag(int dy) { devLV.scroll -= dy; dirty = true; }

// ============================================================ TESTS
static ListView testLV;
static String runningTest;
struct LocalTest { const char* id; const char* name; };
static const LocalTest LOCAL_TESTS[] = {
  {"cyd_colors", "Screen colours"}, {"cyd_touch", "Touch accuracy"}, {"cyd_led", "RGB LED"},
  {"cyd_tone", "Speaker tone (quiet)"}, {"cyd_light", "Light sensor"},
};
static const int N_LOCAL = sizeof(LOCAL_TESTS) / sizeof(LOCAL_TESTS[0]);
static String localLast[N_LOCAL];

static int testCount() { return testsDoc["items"].as<JsonArray>().size() + N_LOCAL + 2; }

void testsDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonArray items = testsDoc["items"].as<JsonArray>();
  int n = testCount();
  clampScroll(testLV, n, 0);
  auto header = [&](int y, const char* s) {
    canvas.setFont(&fonts::Font2); canvas.setTextDatum(ML_DATUM); canvas.setTextColor((uint32_t)C_ACCENT);
    canvas.drawString(s, 6, y + 15);
  };
  auto rowDraw = [&](int y, const char* name, const char* last, const char* when, bool running) {
    canvas.fillRect(0, y, CW - SB_W - 2, testLV.rowH - 2, (uint32_t)C_PANEL);
    canvas.setFont(&fonts::Font2); canvas.setTextDatum(ML_DATUM); canvas.setTextColor((uint32_t)C_TEXT);
    canvas.drawString(name, 10, y + 14);
    const char* badge = running ? "RUN" : (last && *last ? last : "-");
    uint32_t bc = running ? C_BLUE : statusColor(last);
    canvas.fillRoundRect(CW - SB_W - 58, y + 5, 50, 18, 6, bc);
    canvas.setTextColor((uint32_t)C_BG); canvas.setTextDatum(MC_DATUM);
    String b = badge; b.toUpperCase(); canvas.drawString(b, CW - SB_W - 33, y + 14);
    if (when && *when) { canvas.setFont(&fonts::Font0); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(MR_DATUM); canvas.drawString(when, CW - SB_W - 64, y + 14); }
  };
  int i = 0;
  header(i * testLV.rowH - testLV.scroll, linkUp() ? "Robot tests (Orange Pi)" : "Robot tests - no link"); i++;
  for (JsonObject it : items) {
    int y = i * testLV.rowH - testLV.scroll; i++;
    if (y < -testLV.rowH || y > CH) continue;
    rowDraw(y, it["n"] | "?", it["last"] | "", it["when"] | "", runningTest == (const char*)(it["id"] | ""));
  }
  header(i * testLV.rowH - testLV.scroll, "Screen tests (this display)"); i++;
  for (int k = 0; k < N_LOCAL; k++) {
    int y = i * testLV.rowH - testLV.scroll; i++;
    if (y < -testLV.rowH || y > CH) continue;
    rowDraw(y, LOCAL_TESTS[k].name, localLast[k].c_str(), "", false);
  }
  drawScrollBar(canvas, scrollCtl());
}
void testsTap(int x, int y) {
  JsonArray items = testsDoc["items"].as<JsonArray>();
  int i = (y + testLV.scroll) / testLV.rowH;
  int nPi = items.size();
  if (i == 0 || i == nPi + 1) return;  // headers
  if (i >= 1 && i <= nPi) {
    const char* id = items[i - 1]["id"] | "";
    if (!*id || runningTest.length()) return;
    runningTest = id;
    JsonDocument d; d["t"] = "run_test"; d["id"] = id; sendJson(d);
    faceSetMood(M_THINKING, 4000);
    dirty = true;
    return;
  }
  int k = i - nPi - 2;
  if (k >= 0 && k < N_LOCAL) localTestRun(LOCAL_TESTS[k].id);
}
void testsDrag(int dy) { testLV.scroll -= dy; dirty = true; }

void testResult(JsonDocument& d) {
  const char* id = d["id"] | "";
  bool ok = d["ok"] | false;
  if (runningTest == id) runningTest = "";
  for (JsonObject it : testsDoc["items"].as<JsonArray>()) {
    if (!strcmp(it["id"] | "", id)) { it["last"] = ok ? "pass" : "fail"; it["when"] = d["when"] | "now"; }
  }
  String body = String((const char*)(d["sum"] | ""));
  for (JsonVariant l : d["lines"].as<JsonArray>()) { body += "\n"; body += l.as<const char*>(); }
  if (d["show"] | true) modalAsk("", String(ok ? "PASS: " : "FAIL: ") + (const char*)(d["name"] | id), body, "OK", nullptr, nullptr, true);
  faceSetMood(ok ? M_HAPPY : M_SAD, 2500);
  dirty = true;
}

// ---- local (screen-side) tests ----
static void reportLocal(const char* id, bool ok, const String& sum) {
  for (int k = 0; k < N_LOCAL; k++) if (!strcmp(LOCAL_TESTS[k].id, id)) localLast[k] = ok ? "pass" : "fail";
  JsonDocument d; d["t"] = "test_local"; d["id"] = id; d["ok"] = ok; d["sum"] = sum; sendJson(d);
  dirty = true;
}
void onLocalAnswer(const String& id, const String& choice) {
  if (id.startsWith("cyd_")) { reportLocal(id.c_str(), choice == "Yes", "owner answered: " + choice); return; }
  if (id == "panel" && choice == "Switch") {
    S.st7789 = !S.st7789; saveSettings();
    Preferences p; p.begin("cyd", false); p.putBool("panelset", true); p.end();
    delay(50); ESP.restart();
  }
  if (id == "pi_reboot" && choice == "Reboot") { JsonDocument d; d["t"] = "power"; d["action"] = "reboot"; sendJson(d); toast("Orange Pi rebooting..."); }
  if (id == "pi_shutdown" && choice == "Shut down") { JsonDocument d; d["t"] = "power"; d["action"] = "shutdown"; sendJson(d); toast("Orange Pi shutting down..."); }
}
void localTestRun(const char* id) {
  if (!strcmp(id, "cyd_colors")) {
    const uint32_t cols[] = {0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF, 0x000000};
    const char* names[] = {"RED", "GREEN", "BLUE", "WHITE", "BLACK"};
    for (int i = 0; i < 5; i++) {
      lcd.fillScreen(cols[i]);
      lcd.setFont(&fonts::FreeSansBold12pt7b); lcd.setTextDatum(MC_DATUM);
      lcd.setTextColor(i == 3 ? (uint32_t)0x000000 : (uint32_t)0xFFFFFF);
      lcd.drawString(names[i], SCR_W / 2, SCR_H / 2);
      delay(700);
    }
    for (int x = 0; x < SCR_W; x++) {
      uint8_t v = x * 255 / (SCR_W - 1);
      lcd.drawFastVLine(x, 0, 60, rgb(v, 0, 0)); lcd.drawFastVLine(x, 60, 60, rgb(0, v, 0));
      lcd.drawFastVLine(x, 120, 60, rgb(0, 0, v)); lcd.drawFastVLine(x, 180, 60, rgb(v, v, v));
    }
    delay(1500);
    barDirty = true;
    modalAsk("cyd_colors", "Screen colours", "Did you see RED, GREEN, BLUE, WHITE, BLACK in that order, then smooth gradients from dark to bright?\n(If colours looked inverted, turn on Settings > Invert colours.)", "Yes", "No", nullptr, true);
  } else if (!strcmp(id, "cyd_touch")) {
    goPage(P_TOUCHTEST);
  } else if (!strcmp(id, "cyd_led")) {
    setLed(80, 0, 0); delay(500); setLed(0, 80, 0); delay(500); setLed(0, 0, 80); delay(500); setLed(0, 0, 0);
    modalAsk("cyd_led", "RGB LED", "The LED on the back of the screen should have shown red, then green, then blue. Did it?", "Yes", "No", nullptr, true);
  } else if (!strcmp(id, "cyd_tone")) {
    uint8_t v = S.vol; if (!v) S.vol = 1;
    playChime(0); delay(150); playTone(660, 250);
    S.vol = v;
    modalAsk("cyd_tone", "Speaker tone", "A quiet three-note chime then a beep was played on the speaker output. Did you hear it?", "Yes", "No", nullptr, true);
  } else if (!strcmp(id, "cyd_light")) {
    int lo = 4095, hi = 0;
    lcd.fillScreen((uint32_t)C_BG); lcd.setFont(&fonts::FreeSansBold9pt7b); lcd.setTextDatum(MC_DATUM);
    lcd.setTextColor((uint32_t)C_TEXT, (uint32_t)C_BG);
    lcd.drawString("Cover the light sensor,", SCR_W / 2, 60);
    lcd.drawString("then uncover it (5 s)", SCR_W / 2, 84);
    uint32_t t0 = millis();
    while (millis() - t0 < 5000) {
      int v = readLight(); lo = min(lo, v); hi = max(hi, v);
      char b[40]; snprintf(b, sizeof(b), "  now %4d  min %4d  max %4d  ", v, lo, hi);
      lcd.setFont(&fonts::Font2); lcd.drawString(b, SCR_W / 2, 140);
      delay(50);
    }
    barDirty = true;
    char s[64]; snprintf(s, sizeof(s), "raw range %d..%d (delta %d)", lo, hi, hi - lo);
    reportLocal("cyd_light", hi - lo > 100, s);
    modalAsk("", "Light sensor", String(s) + (hi - lo > 100 ? "\nSensor responds." : "\nNo change seen - sensor may be missing or the room is very dark."), "OK", nullptr, nullptr, true);
  }
}

// ============================================================ TOUCH TEST
static int ttX[4] = {30, 290, 30, 290}, ttY[4] = {30, 30, 190, 190};
static bool ttHit[4];
static int ttLastX = -1, ttLastY = -1, ttErr[4];
static uint32_t ttStart = 0;
void touchTestDraw() {
  if (millis() - ttStart > 60000 || ttStart == 0) {  // new session
    ttStart = millis(); for (int i = 0; i < 4; i++) { ttHit[i] = false; ttErr[i] = 0; }
    ttLastX = ttLastY = -1;
  }
  canvas.fillScreen((uint32_t)C_BG);
  canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(MC_DATUM);
  canvas.drawString("Tap the centre of each cross", CW / 2, CH / 2 - 10);
  canvas.drawString("(tap the top bar to leave)", CW / 2, CH / 2 + 10);
  for (int i = 0; i < 4; i++) {
    uint32_t c = ttHit[i] ? C_OK : C_WARN;
    canvas.drawFastHLine(ttX[i] - 12, ttY[i], 25, c); canvas.drawFastVLine(ttX[i], ttY[i] - 12, 25, c);
    canvas.drawCircle(ttX[i], ttY[i], 8, c);
  }
  if (ttLastX >= 0) {
    canvas.fillCircle(ttLastX, ttLastY, 4, (uint32_t)C_ACCENT);
    char b[32]; snprintf(b, sizeof(b), "x=%d y=%d", ttLastX, ttLastY);
    canvas.setTextColor((uint32_t)C_TEXT); canvas.drawString(b, CW / 2, CH / 2 + 40);
  }
}
void touchTestTouch(int x, int y, bool down) {
  ttLastX = x; ttLastY = y; dirty = true;
  if (down) return;
  for (int i = 0; i < 4; i++) {
    int e = (int)sqrtf((x - ttX[i]) * (x - ttX[i]) + (y - ttY[i]) * (y - ttY[i]));
    if (e < 28 && !ttHit[i]) { ttHit[i] = true; ttErr[i] = e; playTone(1200, 30); }
  }
  bool all = true; int worst = 0;
  for (int i = 0; i < 4; i++) { all &= ttHit[i]; worst = max(worst, ttErr[i]); }
  if (all) {
    char s[64]; snprintf(s, sizeof(s), "all 4 targets hit, worst error %d px", worst);
    reportLocal("cyd_touch", worst <= 18, s);
    ttStart = 0;
    goPage(P_TESTS);
    modalAsk("", "Touch accuracy", String(s) + (worst <= 18 ? "\nGood." : "\nConsider Settings > Calibrate touch."), "OK", nullptr, nullptr, true);
  }
}

// ============================================================ LOGS
static String logRobot[40], logSys[40];
static int nRobot = 0, nSys = 0;
static bool logTabSys = false;
static int logScroll = 0;  // lines scrolled up from the bottom
static int logTotal = 0;   // wrapped line count of the current tab
static const int LOG_COLS = 48;
static void pushLine(String* buf, int& n, const char* s) {
  if (n < 40) buf[n++] = s;
  else { for (int i = 1; i < 40; i++) buf[i - 1] = buf[i]; buf[39] = s; }
}
void logsAdd(const char* src, const char* line) {
  if (!strcmp(src, "system")) pushLine(logSys, nSys, line); else pushLine(logRobot, nRobot, line);
  if (page == P_LOGS) dirty = true;
}
void logsSet(JsonDocument& d) {
  bool sys = !strcmp(d["src"] | "robot", "system");
  if (sys) nSys = 0; else nRobot = 0;
  for (JsonVariant l : d["lines"].as<JsonArray>()) {
    if (sys) pushLine(logSys, nSys, l.as<const char*>()); else pushLine(logRobot, nRobot, l.as<const char*>());
  }
  if (page == P_LOGS) dirty = true;
}
void logsDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  button(canvas, 2, 2, 156, 22, "Robot log", logTabSys ? C_PANEL : C_BTN);
  button(canvas, 162, 2, 156, 22, "System (journal)", logTabSys ? C_BTN : C_PANEL);
  String* buf = logTabSys ? logSys : logRobot;
  int n = logTabSys ? nSys : nRobot;
  canvas.setFont(&fonts::Font0); canvas.setTextDatum(TL_DATUM);
  const int lh = 10, top = 28, rows = (CH - top) / lh;
  logTotal = 0;
  for (int i = 0; i < n; i++) logTotal += max(1, (int)((buf[i].length() + LOG_COLS - 1) / LOG_COLS));
  logScroll = constrain(logScroll, 0, max(0, logTotal - rows));
  // render wrapped lines bottom-up
  int y = CH - lh, skipped = 0;
  for (int i = n - 1; i >= 0 && y >= top; i--) {
    String s = buf[i];
    int parts = (s.length() + LOG_COLS - 1) / LOG_COLS; if (parts < 1) parts = 1;
    for (int p = parts - 1; p >= 0 && y >= top; p--) {
      if (skipped < logScroll) { skipped++; continue; }
      uint32_t c = C_TEXT;
      if (s.indexOf("FAIL") >= 0 || s.indexOf("error") >= 0 || s.indexOf("ERR") >= 0) c = C_BAD;
      else if (s.indexOf("PASS") >= 0 || s.indexOf(" ok") >= 0) c = C_OK;
      else if (s.indexOf("warn") >= 0) c = C_WARN;
      canvas.setTextColor(c);
      canvas.drawString(s.substring(p * LOG_COLS, p * LOG_COLS + LOG_COLS), 3, y);
      y -= lh;
    }
  }
  if (n == 0) { canvas.setTextColor((uint32_t)C_DIM); canvas.drawString("no log lines yet", 3, top + 4); }
  drawScrollBar(canvas, scrollCtl());
}
void logsTap(int x, int y) {
  if (y < 26) {
    logTabSys = x >= 160; logScroll = 0; dirty = true;
    sendKV("get", "what", logTabSys ? "syslog" : "logs");
  }
}
void logsDrag(int dy) { logScroll += dy / 4; if (logScroll < 0) logScroll = 0; dirty = true; }

// ============================================================ IDEAS (suggestions / approvals)
static ListView ideaLV;
void ideasDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonArray items = ideasDoc["items"].as<JsonArray>();
  if (items.isNull() || items.size() == 0) {
    canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(MC_DATUM);
    canvas.drawString(linkUp() ? "no suggestions right now" : "no link to Orange Pi", CW / 2, CH / 2);
    return;
  }
  ideaLV.rowH = 40;
  clampScroll(ideaLV, items.size(), 0);
  int i = 0;
  for (JsonObject it : items) {
    int y = i * ideaLV.rowH - ideaLV.scroll; i++;
    if (y < -ideaLV.rowH || y > CH) continue;
    canvas.fillRect(0, y, CW - SB_W - 2, ideaLV.rowH - 2, (uint32_t)C_PANEL);
    const char* pr = it["p"] | "info";
    canvas.fillRect(0, y, 4, ideaLV.rowH - 2, !strcmp(pr, "high") ? C_BAD : (!strcmp(pr, "med") ? C_WARN : C_BLUE));
    canvas.setFont(&fonts::Font2); canvas.setTextDatum(TL_DATUM); canvas.setTextColor((uint32_t)C_TEXT);
    canvas.drawString(it["title"] | "?", 10, y + 3);
    canvas.setFont(&fonts::Font0); canvas.setTextColor((uint32_t)C_DIM);
    String det = (const char*)(it["detail"] | ""); if (det.length() > 47) det = det.substring(0, 45) + "..";
    canvas.drawString(det, 10, y + 23);
  }
  drawScrollBar(canvas, scrollCtl());
}
void ideasTap(int x, int y) {
  JsonArray items = ideasDoc["items"].as<JsonArray>();
  int i = (y + ideaLV.scroll) / ideaLV.rowH;
  if (items.isNull() || i < 0 || i >= (int)items.size()) return;
  JsonObject it = items[i];
  bool action = it["action"] | false;
  modalAsk(String("idea:") + (const char*)(it["id"] | ""), String((const char*)(it["title"] | "Idea")),
           String((const char*)(it["detail"] | "")), action ? "Approve" : "OK", "Later", action ? "Dismiss" : nullptr, false);
}
void ideasDrag(int dy) { ideaLV.scroll -= dy; dirty = true; }

// ============================================================ SETTINGS (scrollable)
// Normally this page draws the Pi's settings list ({"t":"settings"} from robotd/settings.py): the same groups and
// rows as RoboOS Settings and the web app, so a setting is the same everywhere. A tap sends {"t":"set","i":id,"op":..}
// and the Pi answers with the updated list. Without the Pi (link down) the old local screen-only page below is shown.
static const int SROW = 24, SROWS = 11, SR = CW - SB_W - 2;   // SR = right edge of the rows
static int setScroll = 0;
static int setGroup = 0;
static const int TAB_H = 25, RROW = 28;
static bool remoteSettings() { return linkUp() && setDoc["g"].as<JsonArray>().size() > 0; }
static JsonArray remoteRows() {
  JsonArray g = setDoc["g"].as<JsonArray>();
  if (setGroup >= (int)g.size()) setGroup = 0;
  return g[setGroup]["r"].as<JsonArray>();
}
static String fitW(const char* s, int w) {
  String t = s;
  canvas.setFont(&fonts::Font2);
  while (t.length() > 2 && canvas.textWidth(t) > w) t = t.substring(0, t.length() - 3) + "..";
  return t;
}
static void remoteDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonArray g = setDoc["g"].as<JsonArray>();
  int n = g.size(), tw = CW / n;
  for (int i = 0; i < n; i++)
    button(canvas, i * tw + 1, 1, tw - 2, TAB_H - 4, fitW(g[i]["n"] | "?", tw - 6).c_str(), i == setGroup ? C_BTN : C_PANEL2);
  JsonArray rows = remoteRows();
  int view = CH - TAB_H;
  setScroll = constrain(setScroll, 0, max(0, (int)rows.size() * RROW - view));
  canvas.setClipRect(0, TAB_H, CW, view);
  int i = 0;
  for (JsonObject r : rows) {
    int y = TAB_H + i * RROW - setScroll; i++;
    if (y < TAB_H - RROW || y > CH) continue;
    const char* k = r["k"] | "i";
    const char* v = r["v"] | "";
    int cw = *k == 'r' ? 116 : (*k == 't' ? 84 : 150);          // width of the control on the right
    canvas.fillRect(0, y, SR, RROW - 2, (uint32_t)C_PANEL);
    String lab = fitW(r["l"] | "", SR - cw - 12);
    canvas.setTextDatum(ML_DATUM); canvas.setTextColor((uint32_t)C_TEXT);
    canvas.drawString(lab, 6, y + RROW / 2 - 1);
    int by = y + 2, bh = RROW - 6;
    if (*k == 'r') {
      button(canvas, SR - 112, by, 32, bh, "-", C_PANEL2);
      canvas.setFont(&fonts::Font2); canvas.setTextDatum(MC_DATUM); canvas.setTextColor((uint32_t)C_ACCENT);
      canvas.drawString(v, SR - 58, y + RROW / 2 - 1);
      button(canvas, SR - 36, by, 32, bh, "+", C_PANEL2);
    } else if (*k == 't') {
      button(canvas, SR - 84, by, 80, bh, fitW(v, 74).c_str(), (r["on"] | 0) ? C_BTN : C_PANEL2);
    } else if (*k == 'c' || *k == 'a') {
      button(canvas, SR - 150, by, 146, bh, fitW(v, 138).c_str(), *k == 'c' ? C_BTN : C_PANEL2);
    } else {
      canvas.setTextDatum(MR_DATUM); canvas.setTextColor((uint32_t)C_DIM);
      canvas.drawString(fitW(v, 150), SR - 4, y + RROW / 2 - 1);
    }
  }
  canvas.clearClipRect();
  drawScrollBar(canvas, scrollCtl());
}
static bool hit(int x, int bx, int bw);
static void remoteTap(int x, int y) {
  JsonArray g = setDoc["g"].as<JsonArray>();
  if (y < TAB_H) {
    int i = x / (CW / g.size());
    if (i >= 0 && i < (int)g.size()) { setGroup = i; setScroll = 0; dirty = true; }
    return;
  }
  JsonArray rows = remoteRows();
  int i = (y - TAB_H + setScroll) / RROW;
  if (i < 0 || i >= (int)rows.size()) return;
  JsonObject r = rows[i];
  const char* k = r["k"] | "i";
  const char* op = nullptr;
  if (*k == 'r') { if (hit(x, SR - 112, 32)) op = "-"; else if (hit(x, SR - 36, 32)) op = "+"; }
  else if (*k == 't' && x >= SR - 90) op = "toggle";
  else if (*k == 'c' && x >= SR - 156) op = "next";
  else if (*k == 'a' && x >= SR - 156) op = "run";
  if (!op) return;
  JsonDocument d; d["t"] = "set"; d["i"] = (const char*)(r["i"] | ""); d["op"] = op; sendJson(d);
}
static int sY(int i) { return i * SROW - setScroll; }
static void sRow(int i, const char* label) {
  int y = sY(i);
  canvas.fillRect(0, y, SR, SROW - 2, (uint32_t)C_PANEL);
  canvas.setFont(&fonts::Font2); canvas.setTextDatum(ML_DATUM); canvas.setTextColor((uint32_t)C_TEXT);
  canvas.drawString(label, 8, y + SROW / 2 - 1);
}
static void sBtn(int i, int x, int w, const char* l, bool on = false) {
  button(canvas, x, sY(i) + 1, w, SROW - 4, l, on ? C_BTN : C_PANEL2);
}
static void sVal(int i, int x, const String& v) {
  canvas.setFont(&fonts::Font2); canvas.setTextDatum(MC_DATUM); canvas.setTextColor((uint32_t)C_ACCENT);
  canvas.drawString(v, x, sY(i) + SROW / 2 - 1);
}
static bool hit(int x, int bx, int bw) { return x >= bx - 4 && x < bx + bw + 4; }
void settingsDraw() {
  if (remoteSettings()) { remoteDraw(); return; }
  canvas.fillScreen((uint32_t)C_BG);
  setScroll = constrain(setScroll, 0, max(0, SROWS * SROW - CH));
  sRow(0, "Brightness");      sBtn(0, SR - 128, 36, "-"); sVal(0, SR - 73, String(S.bright)); sBtn(0, SR - 52, 36, "+");
  sRow(1, "Theme");           sBtn(1, SR - 144, 140, themeName(S.theme), true);
  sRow(2, "Auto brightness"); sBtn(2, SR - 76, 72, S.autoBright ? "ON" : "OFF", S.autoBright);
  sRow(3, "Volume");          sBtn(3, SR - 128, 36, "-"); sVal(3, SR - 73, S.vol ? String(S.vol) : String("mute")); sBtn(3, SR - 52, 36, "+");
  sRow(4, "AI brain");
  { String l = brainLabel; while (l.length() > 3 && canvas.textWidth(l) > 180) l = l.substring(0, l.length() - 3) + ".."; sBtn(4, SR - 196, 192, l.c_str(), true); }
  sRow(5, "Rotate 180");      sBtn(5, SR - 76, 72, S.rot180 ? "ON" : "OFF", S.rot180);
  sRow(6, "Invert colours");  sBtn(6, SR - 76, 72, S.invert ? "ON" : "OFF", S.invert);
  sRow(7, "Panel driver");    sBtn(7, SR - 106, 102, S.st7789 ? "ST7789" : "ILI9341");
  sRow(8, "Touch");           sBtn(8, SR - 106, 102, "CALIBRATE");
  sRow(9, "Orange Pi");       sBtn(9, SR - 164, 72, "REBOOT"); sBtn(9, SR - 88, 84, "SHUTDOWN");
  sRow(10, "Desktop (RoboOS)"); sBtn(10, SR - 76, 72, "OPEN", true);
  drawScrollBar(canvas, scrollCtl());
}
void settingsTap(int x, int y) {
  if (remoteSettings()) { remoteTap(x, y); return; }
  int i = (y + setScroll) / SROW;
  switch (i) {
    case 0: if (hit(x, SR - 128, 36)) S.bright = max(10, S.bright - 25); else if (hit(x, SR - 52, 36)) S.bright = min(255, S.bright + 25); break;
    case 1: if (hit(x, SR - 144, 140)) { S.theme = (S.theme + 1) % N_THEMES; applyTheme(S.theme); toast(String("Theme: ") + themeName(S.theme), 1500); } break;
    case 2: if (hit(x, SR - 76, 72)) S.autoBright = !S.autoBright; break;
    case 3: if (hit(x, SR - 128, 36)) { if (S.vol) S.vol--; } else if (hit(x, SR - 52, 36)) { if (S.vol < 3) S.vol++; } break;
    case 4: if (hit(x, SR - 196, 192)) { sendEvt("brain_next"); toast("switching AI..."); } return;
    case 5: if (hit(x, SR - 76, 72)) S.rot180 = !S.rot180; break;
    case 6: if (hit(x, SR - 76, 72)) S.invert = !S.invert; break;
    case 7:
      if (hit(x, SR - 106, 102)) {
        modalAsk("panel", "Panel driver", "Switch the display driver and restart the screen? Use this if the picture is garbled or mirrored.", "Switch", "Cancel", nullptr, true);
        return;
      }
      break;
    case 8: if (hit(x, SR - 106, 102)) { sendEvt("calibrating"); runCalibrationPublic(); } return;
    case 9:
      if (hit(x, SR - 164, 72)) modalAsk("pi_reboot", "Reboot Orange Pi?", "The robot brain restarts (about one minute). The face will sleep until it is back.", "Reboot", "Cancel", nullptr, true);
      else if (hit(x, SR - 88, 84)) modalAsk("pi_shutdown", "Shut down Orange Pi?", "Safe shutdown before unplugging power. Unplug only after the green LED on the Orange Pi stops blinking.", "Shut down", "Cancel", nullptr, true);
      return;
    case 10: if (hit(x, SR - 76, 72)) goPage(P_DESKTOP); return;
    default: return;
  }
  saveSettings(); applyDisplaySettings(); dirty = barDirty = true;
}
void settingsDrag(int dy) { setScroll -= dy; dirty = true; }

// ============================================================ ABOUT
void aboutDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  canvas.setFont(&fonts::FreeSansBold9pt7b); canvas.setTextDatum(TL_DATUM); canvas.setTextColor((uint32_t)C_ACCENT);
  canvas.drawString("Robot face + dashboard", 8, 6);
  canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_TEXT);
  char b[80]; int y = 32;
  auto line = [&](const String& s) { canvas.drawString(s, 8, y); y += 17; };
  line(String("Firmware ") + FW_NAME + " " + FW_VER + "  (" + __DATE__ + ")");
  snprintf(b, sizeof(b), "Chip %s rev %d, %d MHz, flash %u MB", ESP.getChipModel(), ESP.getChipRevision(), ESP.getCpuFreqMHz(), (unsigned)(ESP.getFlashChipSize() >> 20)); line(b);
  line(String("Panel ") + panelInfo);
  snprintf(b, sizeof(b), "Heap free %u / min %u bytes", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap()); line(b);
  snprintf(b, sizeof(b), "Canvas %s, touch %s", canvasOk ? "ok" : "FAILED", S.affOk ? "5-point" : (S.calOk ? "4-corner" : "default")); line(b);
  snprintf(b, sizeof(b), "Link: %s, rx %lu, errors %lu", linkUp() ? "up" : "down", (unsigned long)rxCount, (unsigned long)rxErr); line(b);
  line("Serial 921600 baud, JSON lines + JPEG frames");
  button(canvas, 8, 186, 150, 30, "PAIR A PHONE", C_BTN);
  canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(ML_DATUM);
  canvas.drawString("tap elsewhere: back", 170, 201);
}
void aboutTap(int x, int y) {
  if (y >= 182 && x < 162) { sendEvt("pair_request"); toast("making a pairing code..."); return; }
  goPage(P_MENU);
}
void showQr(const char* text, const char* title, const char* sub) {
  if (!*text) return;
  if (screenAsleep) { screenAsleep = false; applyDisplaySettings(); }
  lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_WHITE);
  lcd.qrcode(text, 8, BAR_H + 8, 204, 3);
  lcd.setTextColor((uint32_t)C_BG, (uint32_t)C_WHITE);
  lcd.setTextDatum(TL_DATUM);
  lcd.setFont(&fonts::FreeSansBold9pt7b);
  lcd.setClipRect(218, BAR_H, CW - 218, CH);
  int y = BAR_H + 14;
  String t = title; int sp;
  while (t.length()) { sp = t.indexOf(' ', 8); String w = sp < 0 ? t : t.substring(0, sp); lcd.drawString(w, 220, y); y += 20; t = sp < 0 ? String("") : t.substring(sp + 1); }
  lcd.setFont(&fonts::Font2);
  lcd.drawString(sub, 220, y + 8);
  lcd.drawString("tap to close", 220, SCR_H - 22);
  lcd.clearClipRect();
  imageOverlay = true;
}

// ---- which list is scrollable on the current page --------------------------------------------
static int photoCount();
static ScrollCtl scrollCtl() {
  ScrollCtl s;
  switch (page) {
    case P_DEVICES: s.pos = &devLV.scroll; s.total = MAP_H + devDoc["items"].as<JsonArray>().size() * devLV.rowH; s.view = CH; s.step = devLV.rowH; break;
    case P_TESTS:   s.pos = &testLV.scroll; s.total = testCount() * testLV.rowH; s.view = CH; s.step = testLV.rowH; break;
    case P_IDEAS:   s.pos = &ideaLV.scroll; s.total = ideasDoc["items"].as<JsonArray>().size() * ideaLV.rowH; s.view = CH; s.step = ideaLV.rowH; break;
    case P_LOGS:    s.pos = &logScroll; s.total = logTotal; s.view = (CH - 28) / 10; s.step = 1; s.top = 28; s.h = CH - 28; s.fromBottom = true;
                    s.x = CW - SB_W; return s;
    case P_SETTINGS:
      if (remoteSettings()) {
        s.pos = &setScroll; s.total = remoteRows().size() * RROW; s.view = CH - TAB_H; s.step = RROW; s.top = TAB_H; s.h = CH - TAB_H;
        s.x = CW - SB_W; return s;
      }
      s.pos = &setScroll; s.total = SROWS * SROW; s.view = CH; s.step = SROW; break;
    case P_PHOTOS:  s.pos = &listScroll; s.total = photoCount() * LROW; s.view = CH - 20; s.step = LROW; s.top = 20; s.h = CH - 20;
                    s.x = LIST_W - SB_W; return s;
    default: return s;
  }
  s.x = CW - SB_W; s.top = 0; s.h = CH;
  return s;
}
static int photoCount() { JsonArray it = filesDoc["items"].as<JsonArray>(); return it.isNull() ? 0 : it.size(); }
void photosScrollBarDraw() { drawScrollBar(canvas, scrollCtl()); }
