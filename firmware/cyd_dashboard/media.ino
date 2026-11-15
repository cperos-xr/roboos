// Camera live view, photo browser and full-screen viewer. JPEGs are rendered on the Orange Pi
// to the exact size needed and drawn straight to the panel (they never touch the 8-bit canvas).
#include "common.h"

bool imageOverlay = false;

// ============================================================ CAMERA
static bool camHasFrame = false;
static uint32_t camFrames = 0, camFpsT = 0;
static float camFps = 0;
static uint32_t snapFlashUntil = 0;

static void cameraOverlay() {
  lcd.setFont(&fonts::Font2); lcd.setTextDatum(MC_DATUM);
  lcd.fillRoundRect(4, SCR_H - 30, 58, 24, 10, (uint32_t)C_PANEL2);
  lcd.setTextColor((uint32_t)C_ACCENT); lcd.drawString("READ", 33, SCR_H - 18);
  lcd.fillRoundRect(66, SCR_H - 30, 58, 24, 10, (uint32_t)C_PANEL2);
  lcd.drawString("WHAT?", 95, SCR_H - 18);
  lcd.fillRoundRect(128, SCR_H - 32, 70, 28, 13, snapFlashUntil > millis() ? (uint32_t)C_WHITE : (uint32_t)0xD02020);
  lcd.setTextColor(snapFlashUntil > millis() ? (uint32_t)C_BG : (uint32_t)C_WHITE);
  lcd.drawString("SNAP", 163, SCR_H - 18);
  lcd.fillRoundRect(250, SCR_H - 30, 66, 24, 10, (uint32_t)C_PANEL2);
  lcd.setTextColor((uint32_t)C_ACCENT);
  lcd.drawString("PHOTOS", 283, SCR_H - 18);
  // computer-vision mode button (top right, below the Pi's HUD strip)
  bool cvOn = visionLabel != "OFF";
  lcd.fillRoundRect(236, BAR_H + 20, 80, 22, 9, cvOn ? C_BTN : (uint32_t)C_PANEL2);
  lcd.setTextColor((uint32_t)C_WHITE);
  lcd.drawString(String("CV ") + visionLabel, 276, BAR_H + 31);
  char b[16]; snprintf(b, sizeof(b), "%.1f fps", camFps);
  lcd.setFont(&fonts::Font0); lcd.setTextDatum(TL_DATUM);
  lcd.setTextColor((uint32_t)C_WHITE, (uint32_t)C_BG);
  lcd.drawString(b, 3, BAR_H + 3);
}
void cameraEnter() {
  camHasFrame = false; camFrames = 0; camFpsT = millis(); camFps = 0;
  lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG);
}
void cameraDraw() {
  if (!camHasFrame) {
    lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG);
    lcd.setFont(&fonts::Font2); lcd.setTextDatum(MC_DATUM); lcd.setTextColor((uint32_t)C_DIM);
    lcd.drawString(linkUp() ? "starting camera..." : "no link to Orange Pi", CW / 2, BAR_H + CH / 2);
  }
  cameraOverlay();
  drawToastLcd();
}
void cameraTap(int x, int y) {
  if (x >= 230 && y >= 16 && y < 48) { sendEvt("vision_next"); toast("vision mode...", 900); return; }
  if (y > CH - 44 && x < 64) { sendEvt("read_text"); toast("reading text...", 4000); return; }
  if (y > CH - 44 && x >= 64 && x < 126) { sendEvt("describe"); toast("looking...", 4000); return; }
  if (y > CH - 44 && x >= 126 && x < 210) {
    sendEvt("snap");
    snapFlashUntil = millis() + 250;
    cameraOverlay();
    playTone(1500, 25);
  } else if (y > CH - 44 && x >= 240) {
    goPage(P_PHOTOS);
  }
}

// ============================================================ PHOTOS (file browser)
static String curPath = "photos";
static int sel = -1;
static int listScroll = 0;
static const int LROW = 24, LIST_W = 176;
static bool prevValid = false;
static String prevTag;
static bool lsPending = false;

static String joinPath(const String& a, const char* n) {
  if (!strcmp(n, "..")) { int s = a.lastIndexOf('/'); return s > 0 ? a.substring(0, s) : String(""); }
  return a.length() ? a + "/" + n : String(n);
}
static void requestLs(int pg = 0) {
  JsonDocument d; d["t"] = "ls"; d["path"] = curPath; d["page"] = pg; sendJson(d);
  lsPending = true;
}
static void requestImg(const String& path, int x, int y, int w, int h, const char* slot) {
  JsonDocument d; d["t"] = "get_img"; d["path"] = path; d["x"] = x; d["y"] = y; d["w"] = w; d["h"] = h; d["slot"] = slot;
  sendJson(d);
}
void photosEnter() { sel = -1; prevValid = false; requestLs(); }
void filesReceived() {
  lsPending = false;
  const char* p = filesDoc["path"] | "";
  curPath = p;
  if (page == P_PHOTOS) dirty = true;
}
static JsonArray items() { return filesDoc["items"].as<JsonArray>(); }

void photosDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  JsonArray it = items();
  int n = it.isNull() ? 0 : it.size();
  // path header
  canvas.fillRect(0, 0, LIST_W, 18, (uint32_t)C_PANEL2);
  canvas.setFont(&fonts::Font0); canvas.setTextDatum(ML_DATUM); canvas.setTextColor((uint32_t)C_ACCENT);
  String hdr = "/" + curPath; if (hdr.length() > 28) hdr = ".." + hdr.substring(hdr.length() - 26);
  canvas.drawString(hdr, 4, 9);
  if (n == 0) {
    canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)C_DIM); canvas.setTextDatum(ML_DATUM);
    canvas.drawString(lsPending ? "loading..." : (linkUp() ? "empty folder" : "no link"), 8, 40);
  }
  int maxS = max(0, n * LROW - (CH - 20));
  listScroll = constrain(listScroll, 0, maxS);
  canvas.setClipRect(0, 20, LIST_W, CH - 20);
  for (int i = 0; i < n; i++) {
    int y = 20 + i * LROW - listScroll;
    if (y < 20 - LROW || y > CH) continue;
    JsonObject o = it[i];
    bool dir = o["d"] | false;
    canvas.fillRect(0, y, LIST_W - SB_W - 2, LROW - 2, i == sel ? C_SEL : (uint32_t)C_PANEL);
    canvas.setFont(&fonts::Font2); canvas.setTextDatum(ML_DATUM);
    canvas.setTextColor(dir ? (uint32_t)C_WARN : (uint32_t)C_TEXT);
    String nm = (const char*)(o["n"] | "?");
    if (dir) nm = (nm == "..") ? String("< up") : nm + "/";
    while (nm.length() > 3 && canvas.textWidth(nm) > LIST_W - SB_W - 10) nm = nm.substring(0, nm.length() - 3) + "..";
    canvas.drawString(nm, 5, y + LROW / 2 - 1);
  }
  canvas.clearClipRect();
  extern void photosScrollBarDraw(); photosScrollBarDraw();
  // preview pane
  canvas.fillRect(180, 4, 136, 102, (uint32_t)C_PANEL);
  canvas.drawRect(179, 3, 138, 104, (uint32_t)C_LINE);
  canvas.setFont(&fonts::Font0); canvas.setTextDatum(MC_DATUM); canvas.setTextColor((uint32_t)C_DIM);
  if (sel < 0) canvas.drawString("tap a photo", 248, 55);
  else if (!prevValid) canvas.drawString("loading...", 248, 55);
  if (sel >= 0 && sel < n) {
    JsonObject o = it[sel];
    canvas.setTextDatum(TL_DATUM); canvas.setTextColor((uint32_t)C_TEXT);
    String nm = (const char*)(o["n"] | ""); if (nm.length() > 22) nm = nm.substring(0, 20) + "..";
    canvas.drawString(nm, 180, 112);
    canvas.setTextColor((uint32_t)C_DIM);
    char b[40]; snprintf(b, sizeof(b), "%.1f KB  %s", (o["s"] | 0) / 1024.0f, (const char*)(o["m"] | ""));
    canvas.drawString(b, 180, 124);
    if (o["wh"].is<const char*>()) canvas.drawString(o["wh"].as<const char*>(), 180, 136);
  }
  button(canvas, 180, 152, 66, 28, "OPEN", sel >= 0 ? C_BTN : C_PANEL2);
  button(canvas, 250, 152, 66, 28, "SNAP", (uint32_t)0x8B1F1F);
  button(canvas, 180, 186, 136, 28, "LIVE CAMERA", C_PANEL2);
}
void photosPreviewRedraw() {
  if (prevValid && page == P_PHOTOS && prevLen) lcd.drawJpg(prevBuf, prevLen, 180, BAR_H + 4, 136, 102);
}
static void selectItem(int i) {
  JsonArray it = items();
  if (it.isNull() || i < 0 || i >= (int)it.size()) return;
  JsonObject o = it[i];
  const char* n = o["n"] | "";
  if (o["d"] | false) { curPath = joinPath(curPath, n); sel = -1; prevValid = false; listScroll = 0; requestLs(); dirty = true; return; }
  if (sel == i) { goPage(P_VIEWER); return; }
  sel = i; prevValid = false; prevTag = joinPath(curPath, n);
  requestImg(prevTag, 180, BAR_H + 4, 136, 102, "preview");
  dirty = true;
}
void photosTap(int x, int y) {
  if (x < LIST_W) { if (y >= 20) selectItem((y - 20 + listScroll) / LROW); return; }
  if (y >= 150 && y < 182) {
    if (x < 248) { if (sel >= 0) goPage(P_VIEWER); }
    else { sendEvt("snap"); toast("taking photo..."); }
  } else if (y >= 184) goPage(P_CAMERA);
  else if (y < 108 && sel >= 0) goPage(P_VIEWER);
}
void photosDrag(int dy) { listScroll -= dy; dirty = true; }

// ============================================================ VIEWER
static bool viewerHasImg = false;
static String viewerPath;
void viewerEnter() { viewerPath = ""; viewerHasImg = false; }
static void viewerLoad() {
  JsonArray it = items();
  if (it.isNull() || sel < 0 || sel >= (int)it.size()) return;
  viewerPath = joinPath(curPath, it[sel]["n"] | "");
  viewerHasImg = false;
  requestImg(viewerPath, 0, BAR_H, CW, CH, "full");
}
void viewerDraw() {
  if (!viewerPath.length()) viewerLoad();
  if (!viewerHasImg) {
    lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG);
    lcd.setFont(&fonts::Font2); lcd.setTextDatum(MC_DATUM); lcd.setTextColor((uint32_t)C_DIM);
    lcd.drawString("loading photo...", CW / 2, BAR_H + CH / 2);
  }
}
static void viewerStep(int dir) {
  JsonArray it = items();
  int n = it.isNull() ? 0 : it.size();
  for (int i = sel + dir; i >= 0 && i < n; i += dir) {
    if (!(it[i]["d"] | false)) { sel = i; prevValid = false; viewerLoad(); dirty = true; return; }
  }
  toast(dir > 0 ? "last photo" : "first photo", 1200);
}
void viewerTap(int x, int y) {
  if (x < 90) viewerStep(-1);
  else if (x > 230) viewerStep(1);
  else goPage(P_PHOTOS);
}

// ============================================================ RoboOS desktop (drawn by the Pi)
// The Pi renders a little desktop and sends only the changed rectangle as a JPEG (slot "desk"). Touches on this
// page go to the Pi raw: {"t":"dt","e":"d|m|u","x":..,"y":..} (y relative to the area under the top bar).
static bool deskHasFrame = false;
static uint32_t deskReqMs = 0, deskMoveMs = 0;
static int deskLastX = -1, deskLastY = -1;
void deskEnter() {
  deskHasFrame = false;
  lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)0x008080);
}
void deskDraw() {
  if (!deskHasFrame) {
    lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)0x008080);
    lcd.setFont(&fonts::Font2); lcd.setTextDatum(MC_DATUM); lcd.setTextColor((uint32_t)0xFFFFFF);
    lcd.drawString(linkUp() ? "starting RoboOS..." : "no link to Orange Pi", CW / 2, BAR_H + CH / 2);
  } else if (!toastUntil && millis() - deskReqMs > 400) {
    deskReqMs = millis();
    sendEvt("desk_redraw");                    // something covered the desktop (toast, dialog): ask for a full frame
  }
  drawToastLcd();
}
void deskTouch(char e, int x, int y) {
  uint32_t now = millis();
  if (e == 'm') {                              // throttle drags: ~40 updates a second, only when it really moved
    if (now - deskMoveMs < 25 || (abs(x - deskLastX) < 2 && abs(y - deskLastY) < 2)) return;
  }
  deskMoveMs = now; deskLastX = x; deskLastY = y;
  JsonDocument d; d["t"] = "dt"; char ev[2] = {e, 0}; d["e"] = ev; d["x"] = x; d["y"] = y; sendJson(d);
}

// ============================================================ Game Boy frames (RoboOS Game Boy app, firmware 0.9.3+)
// {"t":"gbf","id":n,"len":N,"crc":..,"x":X,"y":Y,"pal":[c0,c1,c2,c3]} + N bytes: one record per changed row of the
// 160 x 144 picture: [row][n][n bytes]. n == 40: 2-bit pixels, 4 per byte, leftmost pixel in the top two bits;
// n < 40: (count, byte) pairs that expand to those 40 bytes. pal = RGB565 colours of shades 0 (light) .. 3 (dark).
// Drawn straight onto the desktop at x, y + row. The payload arrives in imgBuf like a JPEG: the only memory of its
// own is one 320-byte line. Much smaller than a JPEG of the same picture and nothing to decode.
static const int GB_W = 160, GB_H = 144, GB_ROW_BYTES = 40;
bool gbFrame(JsonDocument& meta, const uint8_t* data, size_t len) {
  if (screenAsleep || page != P_DESKTOP || modalActive() || imageOverlay || !deskHasFrame) return false;
  int x = meta["x"] | 80, y = meta["y"] | 56;
  uint16_t pal[4] = {0xFFFF, 0xAD55, 0x52AA, 0x0000};
  JsonArray p = meta["pal"].as<JsonArray>();
  for (int i = 0; i < 4 && i < (int)p.size(); i++) pal[i] = (uint16_t)(p[i] | 0);
  static uint16_t line[GB_W];
  static uint8_t packed[GB_ROW_BYTES];
  size_t i = 0;
  lcd.startWrite();
  while (i + 2 <= len) {
    int row = data[i], n = data[i + 1];
    i += 2;
    if (row >= GB_H || n > GB_ROW_BYTES || i + n > len) break;      // a damaged record: stop (the CRC makes this rare)
    if (n == GB_ROW_BYTES) {
      memcpy(packed, data + i, GB_ROW_BYTES);
    } else {
      int o = 0;
      for (int k = 0; k + 1 < n; k += 2)
        for (int c = data[i + k]; c > 0 && o < GB_ROW_BYTES; c--) packed[o++] = data[i + k + 1];
      while (o < GB_ROW_BYTES) packed[o++] = 0;
    }
    i += n;
    for (int b = 0; b < GB_ROW_BYTES; b++) {
      uint8_t v = packed[b];
      line[b * 4] = pal[v >> 6]; line[b * 4 + 1] = pal[(v >> 4) & 3];
      line[b * 4 + 2] = pal[(v >> 2) & 3]; line[b * 4 + 3] = pal[v & 3];
    }
    lcd.pushImage(x, y + row, GB_W, 1, (const lgfx::rgb565_t*)line);
  }
  lcd.endWrite();
  if (toastUntil) drawToastLcd();
  return true;
}

// ============================================================ image arrival
bool imageReceived(JsonDocument& meta, const uint8_t* data, size_t len) {
  const char* slot = meta["slot"] | "";
  int x = meta["x"] | 0, y = meta["y"] | BAR_H, w = meta["w"] | CW, h = meta["h"] | CH;
  if (screenAsleep) return false;
  if (!strcmp(slot, "cam")) {
    if (page != P_CAMERA || modalActive() || imageOverlay) return false;
    lcd.drawJpg(data, len, x, y, w, h);
    camHasFrame = true; camFrames++;
    uint32_t now = millis();
    if (now - camFpsT >= 2000) { camFps = camFrames * 1000.0f / (now - camFpsT); camFrames = 0; camFpsT = now; }
    cameraOverlay();
    drawToastLcd();
    return true;
  }
  if (!strcmp(slot, "desk")) {
    if (page != P_DESKTOP || modalActive() || imageOverlay) return false;
    lcd.drawJpg(data, len, x, y, w, h);
    deskHasFrame = true;
    if (toastUntil) drawToastLcd();
    return true;
  }
  if (!strcmp(slot, "preview")) {
    if (page != P_PHOTOS || prevTag != (const char*)(meta["tag"] | "")) return false;
    if (len > prevMax || !prevBuf) return false;
    memcpy(prevBuf, data, len); prevLen = len; prevValid = true;
    dirty = true;
    return true;
  }
  if (!strcmp(slot, "full")) {
    if (page != P_VIEWER || viewerPath != (const char*)(meta["tag"] | "")) return false;
    lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG);
    lcd.drawJpg(data, len, x, y, w, h);
    viewerHasImg = true;
    lcd.setFont(&fonts::Font0); lcd.setTextDatum(BL_DATUM); lcd.setTextColor((uint32_t)C_WHITE, (uint32_t)C_BG);
    String cap = viewerPath.substring(viewerPath.lastIndexOf('/') + 1);
    lcd.drawString(cap + "   < prev | back | next >", 3, SCR_H - 2);
    return true;
  }
  if (!strcmp(slot, "show")) {   // the robot wants to show you something, on any page
    if (modalActive()) return false;
    lcd.fillRect(0, BAR_H, CW, CH, (uint32_t)C_BG);
    lcd.drawJpg(data, len, x, y, w, h);
    const char* cap = meta["caption"] | "";
    if (*cap) {
      lcd.setFont(&fonts::Font2); lcd.setTextDatum(BC_DATUM); lcd.setTextColor((uint32_t)C_WHITE, (uint32_t)C_BG);
      lcd.drawString(cap, CW / 2, SCR_H - 2);
    }
    imageOverlay = true;
    return true;
  }
  return false;
}
