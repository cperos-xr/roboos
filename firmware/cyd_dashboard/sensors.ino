// Optional body-sensor I2C bus on the CYD's CN1 connector (3.3V, GND, IO22, IO27).
// At boot both pin orders are scanned; if an MPU-6050 is found its raw data is streamed to the
// Orange Pi (which does the sensor fusion) whenever the Pi asks with {"t":"imu_stream","on":true}.
#include "common.h"
#include <Wire.h>

static int i2cSda = -1, i2cScl = -1;
static uint8_t foundAddr[16];
static int nFound = 0;
static uint8_t mpuAddr = 0, pcfAddr = 0;
static bool imuStream = false;
static uint16_t imuPeriodMs = 20;
static uint32_t lastImu = 0, i2cOk = 0, i2cFail = 0;

static int scanBus() {
  nFound = 0; mpuAddr = 0; pcfAddr = 0;
  for (uint8_t a = 0x08; a < 0x78 && nFound < 16; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      foundAddr[nFound++] = a;
      if (a == 0x68 || a == 0x69) mpuAddr = a;
      if ((a >= 0x20 && a <= 0x27) || (a >= 0x38 && a <= 0x3F)) pcfAddr = a;
    }
  }
  return nFound;
}

static bool tryBus(int sda, int scl) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) return false;
  Wire.setTimeOut(25);
  if (scanBus() > 0) { i2cSda = sda; i2cScl = scl; return true; }
  return false;
}

static bool mpuWrite(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(mpuAddr); Wire.write(reg); Wire.write(v);
  return Wire.endTransmission() == 0;
}

static void mpuInit() {
  if (!mpuAddr) return;
  mpuWrite(0x6B, 0x01);  // wake, PLL on X gyro
  mpuWrite(0x19, 0x09);  // 100 Hz sample rate
  mpuWrite(0x1A, 0x03);  // DLPF ~44 Hz
  mpuWrite(0x1B, 0x00);  // +-250 dps
  mpuWrite(0x1C, 0x00);  // +-2 g
}

void sensorsInit() {
  if (!tryBus(27, 22) && !tryBus(22, 27)) { Wire.end(); i2cSda = i2cScl = -1; }
  mpuInit();
}

void sensorsHello(JsonDocument& d) {
  JsonObject o = d["i2c"].to<JsonObject>();
  o["sda"] = i2cSda; o["scl"] = i2cScl;
  JsonArray f = o["found"].to<JsonArray>();
  for (int i = 0; i < nFound; i++) { char b[6]; snprintf(b, sizeof(b), "0x%02x", foundAddr[i]); f.add(b); }
}

static void sendScan() {
  JsonDocument d; d["t"] = "i2c"; sensorsHello(d); d["ok"] = i2cOk; d["fail"] = i2cFail; sendJson(d);
}

bool sensorsMsg(const char* t, JsonDocument& d) {
  if (!strcmp(t, "imu_stream")) {
    imuStream = d["on"] | false;
    int hz = d["hz"] | 50; imuPeriodMs = 1000 / constrain(hz, 5, 100);
    return true;
  }
  if (!strcmp(t, "i2c_scan")) {
    if (i2cSda < 0) sensorsInit(); else { scanBus(); mpuInit(); }
    sendScan();
    return true;
  }
  if (!strcmp(t, "pcf_read")) {
    JsonDocument r; r["t"] = "pcf"; r["addr"] = pcfAddr;
    if (pcfAddr && Wire.requestFrom(pcfAddr, (uint8_t)1) == 1) { r["v"] = Wire.read(); i2cOk++; } else { r["err"] = true; i2cFail++; }
    sendJson(r);
    return true;
  }
  if (!strcmp(t, "pcf_write")) {   // quasi-bidirectional: write 1 = input/high, 0 = drive low
    bool ok = false;
    if (pcfAddr) { Wire.beginTransmission(pcfAddr); Wire.write((uint8_t)(d["v"] | 0xFF)); ok = Wire.endTransmission() == 0; }
    JsonDocument r; r["t"] = "pcf_written"; r["ok"] = ok; sendJson(r);
    return true;
  }
  return false;
}

void sensorsPoll() {
  if (!imuStream || !mpuAddr) return;
  uint32_t now = millis();
  if (now - lastImu < imuPeriodMs) return;
  lastImu = now;
  int16_t v[7];
  bool ok = false;
  for (int attempt = 0; attempt < 3 && !ok; attempt++) {
    Wire.beginTransmission(mpuAddr); Wire.write(0x3B);
    if (Wire.endTransmission(false) == 0 && Wire.requestFrom(mpuAddr, (uint8_t)14) == 14) {
      for (int i = 0; i < 7; i++) { int hi = Wire.read(); int lo = Wire.read(); v[i] = (int16_t)((hi << 8) | lo); }
      ok = true; i2cOk++;
    } else i2cFail++;
  }
  if (!ok) return;
  JsonDocument d;
  d["t"] = "imu_raw"; d["ms"] = now;
  JsonArray a = d["a"].to<JsonArray>(); a.add(v[0]); a.add(v[1]); a.add(v[2]);
  JsonArray g = d["g"].to<JsonArray>(); g.add(v[4]); g.add(v[5]); g.add(v[6]);
  d["tr"] = v[3]; d["ok"] = i2cOk; d["fail"] = i2cFail;
  sendJson(d);
}
