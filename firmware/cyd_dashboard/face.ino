// Animated robot face drawn into the 8-bit content canvas (320x220).
#include "common.h"

static const uint32_t FBG = 0x000000;   // the face is always drawn on black

MoodId curMood = M_IDLE;
bool listening = false;
static MoodId baseMood = M_IDLE;
static uint32_t moodUntil = 0;
static float lookX = 0, lookY = 0, tgtX = 0, tgtY = 0;
static uint32_t nextSacc = 0, nextBlink = 0, blinkStart = 0, lookHoldUntil = 0;
static bool blinking = false, doubleBlink = false;
static String sayText;
static uint32_t sayUntil = 0, sayStart = 0;

static const char* MOOD_NAMES[M_COUNT] = {
  "idle", "happy", "curious", "thinking", "warning", "sleepy", "surprised", "sad", "booting", "love"
};

MoodId moodFromName(const char* n) {
  if (!n) return M_IDLE;
  for (int i = 0; i < M_COUNT; i++) if (!strcmp(n, MOOD_NAMES[i])) return (MoodId)i;
  return M_IDLE;
}

// eye colours per theme:   idle      happy     sad       warning   thinking  love      sleepy
static const uint32_t EYES[][7] = {
  /* Original       */ {0x00D8FF, 0x40F0B0, 0x4080FF, 0xFFA020, 0xB080FF, 0xFF5090, 0x0090B0},
  /* Light          */ {0x2F80FF, 0x22C55E, 0x6366F1, 0xF59E0B, 0xA855F7, 0xEC4899, 0x1E5BB8},
  /* Dark           */ {0x4FC1FF, 0x89D185, 0x3794FF, 0xCCA700, 0xC586C0, 0xF48771, 0x2C6D9E},
  /* Solarized Dark */ {0x2AA198, 0x859900, 0x268BD2, 0xB58900, 0x6C71C4, 0xD33682, 0x1A6E68},
  /* Darcula        */ {0xCC7832, 0x6A8759, 0x6897BB, 0xBBB529, 0x9876AA, 0xC77DBB, 0x8A5A2A},
  /* Monokai        */ {0xA6E22E, 0xE6DB74, 0x66D9EF, 0xFD971F, 0xAE81FF, 0xF92672, 0x5A8A1E},
};
uint32_t moodColor(MoodId m) {
  int t = S.theme < sizeof(EYES) / sizeof(EYES[0]) ? S.theme : 0;
  const uint32_t* e = EYES[t];
  switch (m) {
    case M_HAPPY: return e[1];
    case M_SAD: return e[2];
    case M_WARNING: return e[3];
    case M_THINKING: return e[4];
    case M_LOVE: return e[5];
    case M_SLEEPY: return e[6];
    default: return e[0];
  }
}

void faceInit() {
  uint32_t now = millis();
  nextSacc = now + 1200; nextBlink = now + 2500;
}

void faceSetMood(MoodId m, uint32_t holdMs) {
  if (holdMs) { curMood = m; moodUntil = millis() + holdMs; }
  else { baseMood = m; if (!moodUntil) curMood = m; }
  dirty = true;
}

void faceMsg(JsonDocument& d) {
  if (d["mood"].is<const char*>()) {
    MoodId m = moodFromName(d["mood"]);
    uint32_t hold = d["hold"] | 0;
    if (hold) faceSetMood(m, hold); else { moodUntil = 0; faceSetMood(m, 0); curMood = m; }
  }
  if (d["look"].is<JsonArray>()) {
    tgtX = constrain(d["look"][0].as<float>(), -1.0f, 1.0f);
    tgtY = constrain(d["look"][1].as<float>(), -1.0f, 1.0f);
    lookHoldUntil = millis() + (uint32_t)(d["look_ms"] | 2500);
  }
  if (d["say"].is<const char*>()) {
    sayText = (const char*)d["say"];
    sayStart = millis();
    sayUntil = millis() + (uint32_t)(d["sec"] | (2 + sayText.length() / 8)) * 1000;
  }
  if (d["blink"] | false) { blinking = true; blinkStart = millis(); }
  if (d["chime"].is<int>()) playChime(d["chime"].as<int>());
}

void faceTap(int x, int y) {
  if (x > 250 && y > 186) { goPage(P_MENU); return; }
  if (x < 72 && y > 186) {   // push-to-talk
    listening = !listening;
    sendEvt(listening ? "listen" : "listen_stop");
    playTone(listening ? 1400 : 900, 40);
    dirty = true;
    return;
  }
  JsonDocument d; d["t"] = "touch"; d["page"] = "face"; d["x"] = x; d["y"] = y; sendJson(d);
  faceSetMood(M_HAPPY, 1600);
  tgtX = (x - 160) / 160.0f; tgtY = (y - 100) / 110.0f; lookHoldUntil = millis() + 1200;
}

// --- drawing -------------------------------------------------------------
static void eye(int cx, int cy, int w, int h, float open, uint32_t col, MoodId m, bool left) {
  int eh = (int)(h * open); if (eh < 4) eh = 4;
  int r = min(w, eh) / 3;
  if (m == M_SURPRISED) r = min(w, eh) / 2;
  int x0 = cx - w / 2, y0 = cy - eh / 2;
  if (m == M_LOVE) {  // heart eyes
    int s = w / 4;
    canvas.fillCircle(cx - s, cy - s / 2, s + 2, col);
    canvas.fillCircle(cx + s, cy - s / 2, s + 2, col);
    canvas.fillTriangle(cx - 2 * s - 2, cy - s / 4, cx + 2 * s + 2, cy - s / 4, cx, cy + 2 * s + 4, col);
    return;
  }
  canvas.fillRoundRect(x0, y0, w, eh, r, col);
  int inner = left ? x0 + w : x0;       // corner nearest the nose
  int outer = left ? x0 : x0 + w;
  switch (m) {
    case M_HAPPY:   // smiling eyes: cheeks push up from below
      canvas.fillEllipse(cx, cy + eh * 0.62f, w * 0.72f, eh * 0.62f, FBG);
      break;
    case M_SLEEPY:  // heavy lids
      canvas.fillRect(x0 - 2, y0 - 2, w + 4, eh * 0.55f + 2, FBG);
      canvas.fillRect(x0, y0 + eh * 0.55f - 3, w, 4, col);
      break;
    case M_SAD: case M_WARNING:   // outer corners droop
      canvas.fillTriangle(outer, y0 - 2, inner, y0 - 2, outer, y0 + eh * 0.5f, FBG);
      break;
    case M_THINKING:  // one eye squints
      if (!left) canvas.fillRect(x0 - 2, y0 + eh * 0.62f, w + 4, eh, FBG);
      break;
    default: break;
  }
  if (eh > 26 && m != M_SLEEPY && m != M_HAPPY) {
    canvas.fillCircle(cx - w / 5, y0 + eh / 4, max(3, w / 12), (uint32_t)C_WHITE);
  }
}

static void mouth(int cx, int cy, uint32_t col, MoodId m, uint32_t t) {
  if (audioPlaying()) {   // speaking: mouth opens with the loudness of the speech
    int h = 4 + min(26, (int)audioLevel() / 2);
    canvas.fillEllipse(cx, cy + 2, 22, h / 2 + 2, col);
    canvas.fillEllipse(cx, cy + 2, 16, max(1, h / 2 - 2), (uint32_t)0x00303A);
    return;
  }
  switch (m) {
    case M_HAPPY: case M_LOVE:
      canvas.fillArc(cx, cy - 8, 0, 24, 0, 180, col);           // open smile
      canvas.fillArc(cx, cy - 8, 0, 9, 0, 180, (uint32_t)0x00303A);
      break;
    case M_SAD:
      canvas.fillArc(cx, cy + 22, 26, 31, 215, 325, col);
      break;
    case M_SURPRISED:
      canvas.fillEllipse(cx, cy + 2, 13, 17, col);
      canvas.fillEllipse(cx, cy + 2, 7, 11, FBG);
      break;
    case M_CURIOUS:
      canvas.fillCircle(cx + 14, cy + 2, 8, col);
      canvas.fillCircle(cx + 14, cy + 2, 4, FBG);
      break;
    case M_THINKING:
      canvas.fillRoundRect(cx, cy, 34, 5, 2, col);
      break;
    case M_WARNING:
      for (int i = 0; i < 4; i++) canvas.drawWideLine(cx - 30 + i * 15, cy + (i % 2 ? 6 : -2), cx - 15 + i * 15, cy + (i % 2 ? -2 : 6), 2.2f, col);
      break;
    case M_SLEEPY: {
      int r = 5 + (int)(2 * sinf(t / 600.0f));
      canvas.fillCircle(cx, cy + 4, r, col); canvas.fillCircle(cx, cy + 4, r - 3, FBG);
      break;
    }
    case M_BOOTING:
      break;
    default:
      canvas.fillArc(cx, cy - 26, 30, 35, 50, 130, col);
      break;
  }
}

static void extras(MoodId m, uint32_t t, uint32_t col) {
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.setTextDatum(MC_DATUM);
  if (m == M_THINKING) {
    for (int i = 0; i < 3; i++) {
      bool on = ((t / 300) % 4) > (uint32_t)i;
      canvas.fillCircle(250 + i * 16, 28, 5, on ? col : (uint32_t)C_PANEL2);
    }
  } else if (m == M_SLEEPY) {
    float ph = (t % 3000) / 3000.0f;
    canvas.setTextColor(col);
    canvas.drawString("z", 250 + ph * 20, 50 - ph * 30);
    canvas.setFont(&fonts::FreeSansBold9pt7b);
    canvas.drawString("z", 236 + ph * 14, 62 - ph * 22);
  } else if (m == M_WARNING) {
    canvas.fillTriangle(160, 4, 146, 28, 174, 28, (uint32_t)C_WARN);
    canvas.setFont(&fonts::Font2); canvas.setTextColor((uint32_t)FBG);
    canvas.drawString("!", 160, 20);
  } else if (m == M_CURIOUS) {
    canvas.setTextColor(col);
    canvas.drawString("?", 262, 30 + (int)(3 * sinf(t / 300.0f)));
  }
}

void faceDraw() {
  uint32_t t = millis();
  if (moodUntil && t > moodUntil) { moodUntil = 0; curMood = baseMood; }
  MoodId m = curMood;
  // with no Orange Pi link after boot, look sleepy and say so
  bool noLink = !linkUp() && t > 6000;
  if (noLink && m != M_WARNING) m = M_SLEEPY;

  // gaze
  if (t > nextSacc && t > lookHoldUntil) {
    tgtX = (random(-100, 101) / 100.0f) * 0.8f;
    tgtY = (random(-100, 101) / 100.0f) * 0.5f;
    if (random(0, 3) == 0) { tgtX = 0; tgtY = 0; }
    if (m == M_THINKING) { tgtX = 0.6f; tgtY = -0.7f; }
    nextSacc = t + random(1200, 4500);
  }
  lookX += (tgtX - lookX) * 0.35f;
  lookY += (tgtY - lookY) * 0.35f;

  // blink
  float open = 1.0f;
  if (!blinking && t > nextBlink) {
    blinking = true; blinkStart = t; doubleBlink = random(0, 5) == 0;
    nextBlink = t + random(2200, 6000);
  }
  if (blinking) {
    uint32_t dt = t - blinkStart;
    const uint32_t B = 170;
    if (dt < B) open = fabsf(1.0f - 2.0f * dt / (float)B);
    else if (doubleBlink && dt < 2 * B + 60) { open = dt < B + 60 ? 1.0f : fabsf(1.0f - 2.0f * (dt - B - 60) / (float)B); }
    else blinking = false;
  }
  if (m == M_SLEEPY) open *= 0.9f;
  if (m == M_BOOTING) open = 0.08f + 0.92f * fminf(1.0f, (t % 2500) / 2500.0f);

  uint32_t col = moodColor(m);
  canvas.fillScreen((uint32_t)FBG);
  float bob = 2.0f * sinf(t / 900.0f);
  int dx = (int)(lookX * 26), dy = (int)(lookY * 14 + bob);
  int ew = 68, eh = 92, sep = 52;
  int ewL = ew, ehL = eh, ewR = ew, ehR = eh;
  if (m == M_CURIOUS) { ehL = eh * 1.12f; ehR = eh * 0.78f; }
  if (m == M_SURPRISED) { ewL = ewR = ew * 0.86f; ehL = ehR = eh * 1.08f; }
  eye(160 - sep - ew / 2 + 24 + dx, 88 + dy, ewL, ehL, open, col, m, true);
  eye(160 + sep + ew / 2 - 24 + dx, 88 + dy, ewR, ehR, open, col, m, false);
  mouth(160 + dx / 2, 170 + dy / 2, col, m, t);
  extras(m, t, col);

  // speech line (scrolls when too long)
  if (sayUntil && t < sayUntil && sayText.length()) {
    canvas.setFont(&fonts::FreeSansBold9pt7b);
    int tw = canvas.textWidth(sayText);
    canvas.fillRect(0, 194, CW, 26, (uint32_t)C_PANEL);
    canvas.setTextColor((uint32_t)C_TEXT);
    canvas.setTextDatum(ML_DATUM);
    if (tw <= CW - 140) {
      canvas.drawString(sayText, 74 + (CW - 140 - tw) / 2, 207);
    } else {
      int span = tw + 60;
      int off = ((t - sayStart) / 18) % span;
      canvas.setClipRect(74, 194, CW - 140, 26);
      canvas.drawString(sayText, 78 - off + (t - sayStart < 800 ? off : 0), 207);
      canvas.clearClipRect();
    }
  } else if (noLink) {
    canvas.setFont(&fonts::Font2);
    canvas.setTextColor((uint32_t)C_DIM);
    canvas.setTextDatum(ML_DATUM);
    canvas.drawString("waiting for Orange Pi...", 78, 207);
  }
  // talk pill (push-to-talk) and menu pill
  canvas.fillRoundRect(4, 194, 66, 24, 10, listening ? (uint32_t)0xC02828 : (uint32_t)C_PANEL2);
  canvas.setFont(&fonts::Font2); canvas.setTextDatum(MC_DATUM);
  canvas.setTextColor(listening ? (uint32_t)C_WHITE : (uint32_t)C_ACCENT);
  canvas.drawString(listening ? "LISTEN" : "TALK", 37, 206);
  canvas.fillRoundRect(256, 194, 60, 24, 10, (uint32_t)C_PANEL2);
  canvas.setFont(&fonts::Font2);
  canvas.setTextColor((uint32_t)C_ACCENT);
  canvas.setTextDatum(MC_DATUM);
  canvas.drawString("MENU", 286, 206);
}
