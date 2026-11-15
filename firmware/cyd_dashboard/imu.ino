// Real-time IMU visualisation: attitude indicator, 3D body box, raw values and bump graph.
// Orientation (pitch/roll/yaw) is computed on the Orange Pi and sent as {"t":"imu",...} at ~25 Hz.
#include "common.h"

struct ImuData {
  float p = 0, r = 0, y = 0, ax = 0, ay = 0, az = 1, gx = 0, gy = 0, gz = 0, tc = 0, err = 0, hz = 0;
  String src = "-";
  bool valid = false;
  uint32_t at = 0;
} IM;
static float bump[100];
static int bumpHead = 0;

void imuMsg(JsonDocument& d) {
  IM.p = d["p"] | 0.0f; IM.r = d["r"] | 0.0f; IM.y = d["y"] | 0.0f;
  IM.ax = d["ax"] | 0.0f; IM.ay = d["ay"] | 0.0f; IM.az = d["az"] | 0.0f;
  IM.gx = d["gx"] | 0.0f; IM.gy = d["gy"] | 0.0f; IM.gz = d["gz"] | 0.0f;
  IM.tc = d["tc"] | 0.0f; IM.err = d["err"] | 0.0f; IM.hz = d["hz"] | 0.0f;
  IM.src = (const char*)(d["src"] | "pi");
  IM.valid = true; IM.at = millis();
  float mag = sqrtf(IM.ax * IM.ax + IM.ay * IM.ay + IM.az * IM.az);
  bump[bumpHead] = mag - 1.0f; bumpHead = (bumpHead + 1) % 100;
  if (page == P_IMU) dirty = true;
}

static void horizon(int cx, int cy, int R) {
  const uint32_t SKY = 0x2D7FD8, GROUND = 0x8A5A2B;
  float roll = IM.r * DEG_TO_RAD, ppd = 2.2f;
  float ux = cosf(roll), uy = -sinf(roll);   // horizon direction (screen coords, y down)
  float nx = -sinf(roll), ny = -cosf(roll);  // points toward the sky
  float c0x = cx - nx * IM.p * ppd, c0y = cy - ny * IM.p * ppd;
  const float L = 260;
  canvas.setClipRect(cx - R, cy - R, 2 * R + 1, 2 * R + 1);
  canvas.fillCircle(cx, cy, R, GROUND);
  float p1x = c0x - ux * L, p1y = c0y - uy * L, p2x = c0x + ux * L, p2y = c0y + uy * L;
  float p3x = p2x + nx * L, p3y = p2y + ny * L, p4x = p1x + nx * L, p4y = p1y + ny * L;
  canvas.fillTriangle(p1x, p1y, p2x, p2y, p3x, p3y, SKY);
  canvas.fillTriangle(p1x, p1y, p3x, p3y, p4x, p4y, SKY);
  canvas.drawWideLine(p1x, p1y, p2x, p2y, 1.0f, (uint32_t)C_WHITE);
  canvas.setFont(&fonts::Font0); canvas.setTextColor((uint32_t)C_WHITE); canvas.setTextDatum(MC_DATUM);
  for (int k = -40; k <= 40; k += 10) {
    if (!k) continue;
    float mx = c0x + nx * k * ppd, my = c0y + ny * k * ppd;
    float hl = (k % 20 == 0) ? 24 : 13;
    canvas.drawLine(mx - ux * hl, my - uy * hl, mx + ux * hl, my + uy * hl, (uint32_t)C_WHITE);
    if (k % 20 == 0) canvas.drawNumber(abs(k), mx + ux * (hl + 9), my + uy * (hl + 9));
  }
  canvas.fillArc(cx, cy, R, R + 40, 0, 360, C_BG);   // mask the square corners
  canvas.clearClipRect();
  canvas.drawCircle(cx, cy, R, (uint32_t)C_LINE);
  // fixed bezel roll marks and sky pointer
  for (int a : {-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60}) {
    float t = (-90 + a) * DEG_TO_RAD; int l = (a % 30 == 0) ? 9 : 5;
    canvas.drawLine(cx + cosf(t) * (R - 1), cy + sinf(t) * (R - 1), cx + cosf(t) * (R - l), cy + sinf(t) * (R - l), (uint32_t)C_WHITE);
  }
  float pt = (-90 - IM.r) * DEG_TO_RAD;
  float tx = cx + cosf(pt) * (R - 12), ty = cy + sinf(pt) * (R - 12);
  float px = -sinf(pt) * 5, py = cosf(pt) * 5;
  canvas.fillTriangle(cx + cosf(pt) * (R - 3), cy + sinf(pt) * (R - 3), tx + px, ty + py, tx - px, ty - py, (uint32_t)C_WARN);
  // the robot symbol (fixed)
  canvas.fillRect(cx - 42, cy - 1, 26, 4, (uint32_t)C_WARN);
  canvas.fillRect(cx + 17, cy - 1, 26, 4, (uint32_t)C_WARN);
  canvas.fillCircle(cx, cy + 1, 4, (uint32_t)C_WARN);
}

static void bodyBox(int cx, int cy, float S) {
  // body box: x forward, y left, z up
  const float a = 1.0f, b = 0.7f, c = 0.45f;
  float V[8][3] = {{a, b, c}, {a, -b, c}, {a, -b, -c}, {a, b, -c}, {-a, b, c}, {-a, -b, c}, {-a, -b, -c}, {-a, b, -c}};
  float r = IM.r * DEG_TO_RAD, p = -IM.p * DEG_TO_RAD, y = IM.y * DEG_TO_RAD;
  const float vy = 35 * DEG_TO_RAD, vp = 22 * DEG_TO_RAD;  // fixed viewing angle
  int sx[8], sy[8];
  for (int i = 0; i < 8; i++) {
    float x = V[i][0], yy = V[i][1], z = V[i][2], t;
    t = yy * cosf(r) - z * sinf(r); z = yy * sinf(r) + z * cosf(r); yy = t;    // roll about x
    t = x * cosf(p) + z * sinf(p); z = -x * sinf(p) + z * cosf(p); x = t;      // pitch about y
    t = x * cosf(y) - yy * sinf(y); yy = x * sinf(y) + yy * cosf(y); x = t;    // yaw about z
    t = x * cosf(vy) - yy * sinf(vy); yy = x * sinf(vy) + yy * cosf(vy); x = t; // view yaw
    float zz = z * cosf(vp) - x * sinf(vp);                                     // view pitch
    sx[i] = cx + (int)(-yy * S);
    sy[i] = cy - (int)(zz * S);
  }
  static const uint8_t E[12][2] = {{0,1},{1,2},{2,3},{3,0},{4,5},{5,6},{6,7},{7,4},{0,4},{1,5},{2,6},{3,7}};
  for (int i = 0; i < 12; i++) {
    bool front = E[i][0] < 4 && E[i][1] < 4;
    canvas.drawWideLine(sx[E[i][0]], sy[E[i][0]], sx[E[i][1]], sy[E[i][1]], front ? 1.6f : 1.0f, front ? (uint32_t)C_ACCENT : (uint32_t)C_DIM);
  }
  // little "eyes" on the front face so forward is obvious
  int fx = (sx[0] + sx[1] + sx[2] + sx[3]) / 4, fy = (sy[0] + sy[1] + sy[2] + sy[3]) / 4;
  canvas.fillCircle(fx, fy, 3, (uint32_t)C_ACCENT);
}

void imuDraw() {
  canvas.fillScreen((uint32_t)C_BG);
  bool fresh = IM.valid && millis() - IM.at < 1500;
  horizon(88, 90, 82);
  canvas.setFont(&fonts::Font2); canvas.setTextDatum(TL_DATUM);
  char b[64];
  canvas.setTextColor((uint32_t)C_TEXT);
  snprintf(b, sizeof(b), "Pitch %+6.1f", IM.p); canvas.drawString(b, 4, 178);
  snprintf(b, sizeof(b), "Roll %+6.1f", IM.r); canvas.drawString(b, 92, 178);
  canvas.setTextColor((uint32_t)C_DIM);
  snprintf(b, sizeof(b), "Yaw %+6.1f (gyro, drifts)", IM.y); canvas.drawString(b, 4, 196);

  canvas.fillRoundRect(180, 2, 138, 96, 8, (uint32_t)C_PANEL);
  bodyBox(249, 52, 30);

  canvas.setFont(&fonts::Font0); canvas.setTextColor((uint32_t)C_TEXT);
  float mag = sqrtf(IM.ax * IM.ax + IM.ay * IM.ay + IM.az * IM.az);
  snprintf(b, sizeof(b), "acc %+5.2f %+5.2f %+5.2f", IM.ax, IM.ay, IM.az); canvas.drawString(b, 182, 104);
  snprintf(b, sizeof(b), "|a| %.3f g", mag); canvas.drawString(b, 182, 114);
  snprintf(b, sizeof(b), "gyr %+5.0f %+5.0f %+5.0f", IM.gx, IM.gy, IM.gz); canvas.drawString(b, 182, 124);
  canvas.setTextColor(fresh ? (uint32_t)C_DIM : (uint32_t)C_BAD);
  snprintf(b, sizeof(b), "%s %.0fHz %.1fC err%.1f%%", fresh ? IM.src.c_str() : "NO DATA", IM.hz, IM.tc, IM.err);
  canvas.drawString(b, 182, 134);

  // bump / vibration graph (|a| - 1 g)
  canvas.fillRect(180, 146, 138, 40, (uint32_t)C_PANEL);
  canvas.drawFastHLine(180, 166, 138, (uint32_t)C_LINE);
  int px = -1, py = 0;
  for (int i = 0; i < 100; i++) {
    float v = bump[(bumpHead + i) % 100];
    int x = 182 + i * 134 / 100, y = 166 - (int)constrain(v * 40, -19.0f, 19.0f);
    if (px >= 0) canvas.drawLine(px, py, x, y, fabsf(v) > 0.3f ? (uint32_t)C_WARN : (uint32_t)C_OK);
    px = x; py = y;
  }
  canvas.setTextColor((uint32_t)C_DIM); canvas.drawString("bumps", 184, 148);

  button(canvas, 180, 190, 66, 28, "LEVEL", C_PANEL2);
  button(canvas, 252, 190, 66, 28, "GYRO 0", C_PANEL2);
  if (!fresh) {
    canvas.setFont(&fonts::FreeSansBold9pt7b); canvas.setTextColor((uint32_t)C_WARN); canvas.setTextDatum(MC_DATUM);
    canvas.drawString(linkUp() ? "waiting for IMU..." : "no link", 88, 90);
  }
}

void imuTap(int x, int y) {
  if (y >= 186 && x >= 180 && x < 248) { sendEvt("imu_level"); toast("Level set: current pose = flat"); }
  else if (y >= 186 && x >= 250) { sendEvt("imu_gyro_cal"); toast("Hold still: calibrating gyro..."); }
}
