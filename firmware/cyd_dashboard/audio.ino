// Streamed speech/audio playback on the CYD speaker output (DAC channel 1 = GPIO26 -> amplifier).
// The Pi sends {"t":"pcm","id":n,"len":N,"rate":8000,"crc":..} followed by N unsigned 8-bit mono samples.
// Samples go into a ring buffer that a hardware timer drains into the DAC. A chunk is acknowledged only
// once it fits in the ring, which paces the sender (no overflow, ~2 s of buffering).
#include "common.h"
#include "driver/dac_oneshot.h"
#include "hal/dac_ll.h"

#define AUD_RING 16384   // power of two
static uint8_t audRing[AUD_RING];
static volatile uint32_t audRd = 0, audWr = 0;
static volatile uint8_t audLvl = 0;
static hw_timer_t* audTimer = nullptr;
static dac_oneshot_handle_t audDac = nullptr;
static uint32_t audRate = 8000, audLastData = 0;
static bool audPending = false;
static size_t audPendLen = 0;
static uint32_t audPendId = 0;
static const uint8_t* audPendData = nullptr;

void ARDUINO_ISR_ATTR audIsr() {
  if (audRd != audWr) {
    uint8_t s = audRing[audRd & (AUD_RING - 1)];
    audRd = audRd + 1;
    dac_ll_update_output_value(DAC_CHAN_1, s);
    uint8_t a = s > 128 ? s - 128 : 128 - s;
    audLvl = (uint8_t)((audLvl * 15 + a) >> 4);
  } else {
    audLvl = audLvl >> 1;
  }
}

static void audStart(uint32_t rate) {
  if (audTimer && rate == audRate) return;
  if (!audDac) {
    dac_oneshot_config_t c = {};
    c.chan_id = DAC_CHAN_1;
    if (dac_oneshot_new_channel(&c, &audDac) != ESP_OK) { audDac = nullptr; return; }
    dac_oneshot_output_voltage(audDac, 128);
  }
  if (audTimer) { timerEnd(audTimer); audTimer = nullptr; }
  audRate = constrain(rate, 4000, 22050);
  audTimer = timerBegin(1000000);
  timerAttachInterrupt(audTimer, &audIsr);
  timerAlarm(audTimer, 1000000 / audRate, true, 0);
}

void audioStop() {
  if (audTimer) { timerEnd(audTimer); audTimer = nullptr; }
  if (audDac) { dac_oneshot_output_voltage(audDac, 128); dac_oneshot_del_channel(audDac); audDac = nullptr; }
  audRd = audWr = 0; audLvl = 0;
}

// Lip sync for speech the Pi plays on its own speaker: {"t":"lips","ms":40,"lv":"..."} gives one loudness
// level per frame as a character '0'..'O' (0..31), so the mouth moves even when this board makes no sound.
static char lipBuf[2400];
static uint16_t lipN = 0, lipMs = 40;
static uint32_t lipT0 = 0;
void lipsStart(const char* lv, int ms) {
  size_t n = strlen(lv);
  if (n > sizeof(lipBuf)) n = sizeof(lipBuf);
  memcpy(lipBuf, lv, n);
  lipN = n; lipMs = constrain(ms, 10, 200); lipT0 = millis();
}
static int lipIdx() {
  if (!lipN) return -1;
  uint32_t i = (millis() - lipT0) / lipMs;
  if (i >= lipN) { lipN = 0; return -1; }
  return (int)i;
}

bool audioPlaying() { return (audTimer && (audRd != audWr || millis() - audLastData < 250)) || lipIdx() >= 0; }
uint8_t audioLevel() {
  int i = lipIdx();
  uint8_t l = i >= 0 ? (uint8_t)constrain((lipBuf[i] - '0') * 2, 0, 62) : 0;
  return max(l, (uint8_t)audLvl);
}

static uint32_t audFree() { return AUD_RING - 1 - (audWr - audRd); }

static void audEnqueue(const uint8_t* d, size_t n) {
  // volume: 0 mute, 1 quiet, 2 medium, 3 loud (scale around the 128 midpoint)
  static const uint16_t gain[4] = {0, 70, 150, 256};
  uint16_t g = gain[S.vol & 3];
  uint32_t w = audWr;
  for (size_t i = 0; i < n; i++) {
    int v = 128 + (((int)d[i] - 128) * g >> 8);
    audRing[w & (AUD_RING - 1)] = (uint8_t)v;
    w++;
  }
  audWr = w;
  audLastData = millis();
}

static void audAck(uint32_t id, bool ok) {
  JsonDocument d; d["t"] = "ack"; d["id"] = id; d["ok"] = ok; d["free"] = audFree(); sendJson(d);
}

// called when a complete pcm chunk has arrived (data lives in imgBuf until we ack)
bool audioReceived(JsonDocument& meta, const uint8_t* data, size_t len, uint32_t id) {
  if (len >= AUD_RING) { audAck(id, false); return true; }
  audStart(meta["rate"] | 8000);
  if (!audTimer) { audAck(id, false); return true; }
  if (audFree() >= len) { audEnqueue(data, len); audAck(id, true); return true; }
  audPending = true; audPendLen = len; audPendId = id; audPendData = data;
  return false;  // ack later from audioPoll()
}

void audioPoll() {
  if (audPending && audFree() >= audPendLen) {
    audEnqueue(audPendData, audPendLen);
    audPending = false;
    audAck(audPendId, true);
  }
  // power the DAC down after a short silence so the amplifier stays quiet
  if (audTimer && audRd == audWr && !audPending && millis() - audLastData > 1500) audioStop();
}

bool audioBusy() { return audPending; }
