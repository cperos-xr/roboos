#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include "lgfx_cyd.h"

#define FW_NAME "cyd-dashboard"
#define FW_VER  "0.9.3"

#define SCR_W 320
#define SCR_H 240
#define BAR_H 20
#define CW 320          // content area width
#define CH 220          // content area height (below the top bar)

#define LINE_MAX 8192
#define IMG_MAX  (48 * 1024)
#define PREV_MAX (16 * 1024)

// ---------- colors (always uint32_t so LovyanGFX treats them as RGB888) ----------
static inline uint32_t rgb(uint8_t r, uint8_t g, uint8_t b) { return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b; }
// theme colours (set by applyTheme; see THEMES in cyd_dashboard.ino)
extern uint32_t C_BG, C_PANEL, C_PANEL2, C_LINE, C_TEXT, C_DIM, C_ACCENT, C_BTN, C_SEL, C_OK, C_WARN, C_BAD, C_GRAY, C_BLUE;
static const uint32_t C_WHITE = 0xFFFFFF;
extern const int N_THEMES;
void applyTheme(int i);
const char* themeName(int i);

enum PageId : uint8_t {
  P_FACE, P_MENU, P_STATUS, P_DEVICES, P_TESTS, P_CAMERA, P_PHOTOS, P_VIEWER,
  P_IMU, P_LOGS, P_IDEAS, P_SETTINGS, P_ABOUT, P_TOUCHTEST, P_DESKTOP, P_COUNT
};

enum MoodId : uint8_t {
  M_IDLE, M_HAPPY, M_CURIOUS, M_THINKING, M_WARNING, M_SLEEPY, M_SURPRISED, M_SAD, M_BOOTING, M_LOVE, M_COUNT
};

struct ListView { int scroll = 0; int rowH = 30; };

struct Settings {
  uint8_t bright = 180;
  bool autoBright = false;
  uint8_t vol = 1;          // 0 mute .. 3 loudest (DAC cosine attenuation)
  bool rot180 = false;
  bool invert = false;
  bool st7789 = false;
  bool calOk = false;
  uint16_t cal[8] = {0};
  uint8_t theme = 0;       // 0 Original, 1 Light, 2 Dark, 3 Solarized Dark, 4 Darcula, 5 Monokai
  bool affOk = false;      // 5-point affine calibration (preferred)
  float aff[6] = {0};
};

// ---------- globals (defined in cyd_dashboard.ino) ----------
extern LGFX lcd;
extern LGFX_Sprite canvas;
extern LGFX_Sprite bar;
extern Settings S;
extern PageId page;
extern bool dirty, barDirty;
extern uint32_t lastRxMs, rxCount, rxErr;
extern String piTime, panelInfo;
extern uint8_t* imgBuf;
extern uint8_t* prevBuf;
extern size_t imgMax, prevMax;   // buffer sizes actually allocated (reported to the Pi)
extern size_t prevLen;
extern bool screenAsleep;
extern bool canvasOk;

extern JsonDocument statusDoc, devDoc, testsDoc, filesDoc, ideasDoc;
extern JsonDocument setDoc;      // the Pi's settings list (robotd/settings.py), drawn by the Settings page

// ---------- core helpers ----------
void sendJson(JsonDocument& d);
void sendEvt(const char* t);
void sendKV(const char* t, const char* k, const char* v);
void goPage(PageId p);
const char* pageName(PageId p);
void toast(const String& s, uint32_t ms = 2500);
bool linkUp();
void pushCanvas();
void drawToastOn(LGFX_Sprite& c);
void drawToastLcd();
void saveSettings();
void putSettings(JsonObject o);
void applyDisplaySettings();
void playChime(uint8_t kind);
void playTone(uint16_t hz, uint16_t ms);
void setLed(uint8_t r, uint8_t g, uint8_t b);
int readLight();

// modal dialogs
void modalAsk(const String& id, const String& title, const String& body, const char* o1, const char* o2 = nullptr, const char* o3 = nullptr, bool local = false);
bool modalActive();
void modalDraw(LGFX_Sprite& c);
void modalTap(int x, int y);
void onLocalAnswer(const String& id, const String& choice);

// drawing helpers
void button(LGFX_Sprite& c, int x, int y, int w, int h, const char* label, uint32_t bg, uint32_t fg = C_TEXT);
void wrapText(LGFX_Sprite& c, const String& s, int x, int y, int w, int lineH, int maxLines);
void statusDot(LGFX_Sprite& c, int x, int y, const char* st);
uint32_t statusColor(const char* st);

// face
void faceInit();
void faceDraw();
void faceTap(int x, int y);
void faceMsg(JsonDocument& d);
void faceSetMood(MoodId m, uint32_t holdMs = 0);
MoodId moodFromName(const char* n);
uint32_t moodColor(MoodId m);
extern MoodId curMood;

// pages
void menuDraw();     void menuTap(int x, int y);
void statusDraw();
void devicesDraw();  void devicesTap(int x, int y);  void devicesDrag(int dy);
void testsDraw();    void testsTap(int x, int y);    void testsDrag(int dy);
void testResult(JsonDocument& d);
void logsDraw();     void logsTap(int x, int y);     void logsDrag(int dy);
void logsAdd(const char* src, const char* line);
void logsSet(JsonDocument& d);
void ideasDraw();    void ideasTap(int x, int y);    void ideasDrag(int dy);
void settingsDraw(); void settingsTap(int x, int y); void settingsDrag(int dy);
void aboutDraw();
void touchTestDraw(); void touchTestTouch(int x, int y, bool down);
void localTestRun(const char* id);

// media
void cameraEnter();  void cameraDraw();  void cameraTap(int x, int y);
void photosEnter();  void photosDraw();  void photosTap(int x, int y); void photosDrag(int dy);
void viewerDraw();   void viewerTap(int x, int y); void viewerEnter();
void photosPreviewRedraw();
void filesReceived();
bool imageReceived(JsonDocument& meta, const uint8_t* data, size_t len);  // returns true if drawn
bool gbFrame(JsonDocument& meta, const uint8_t* data, size_t len);        // Game Boy rows on the desktop (media.ino)
extern bool imageOverlay;

// body sensors on CN1
void sensorsInit(); void sensorsPoll(); void sensorsHello(JsonDocument& d);
bool sensorsMsg(const char* t, JsonDocument& d);
void runCalibrationPublic();

// audio (speech playback)
bool audioReceived(JsonDocument& meta, const uint8_t* data, size_t len, uint32_t id);
void audioPoll(); void audioStop(); bool audioPlaying(); uint8_t audioLevel(); bool audioBusy(); void lipsStart(const char* lv, int ms);
void deskEnter(); void deskDraw(); void deskTouch(char e, int x, int y);
extern bool listening;
// classic (Windows 95 style) scroll bar shared by all list pages; coordinates are content-area relative
#define SB_W 24
struct MapNode { const char* k; const char* label; int16_t x, y; };
struct MapEdge { const char* a; const char* b; const char* label; };
struct ScrollCtl { int* pos = nullptr; int total = 0, view = 0, step = 0, x = 0, top = 0, h = 0; bool fromBottom = false; };
bool scrollBarHit(int x, int y); void scrollBarTap(int x, int y); void scrollBarDrag(int dy); bool scrollArrowZone(int x, int y);
extern String brainLabel;
extern String visionLabel;
void aboutTap(int x, int y);
void showQr(const char* text, const char* title, const char* sub);

// imu
void imuDraw();      void imuTap(int x, int y);
void imuMsg(JsonDocument& d);
