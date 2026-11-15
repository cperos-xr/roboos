#pragma once
// Display/touch configuration for the ESP32-2432S028R "Cheap Yellow Display".
// LCD on SPI2 (HSPI): SCLK14 MOSI13 MISO12 CS15 DC2, backlight GPIO21.
// Touch XPT2046 on its own SPI3 (VSPI): SCLK25 MOSI32 MISO39 CS33 IRQ36.
// Two panel drivers are configured; the single-USB board uses ILI9341, the "2USB" board ST7789.
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

class LGFX : public lgfx::LGFX_Device {
public:
  lgfx::Panel_ILI9341 panel_ili;
  lgfx::Panel_ST7789  panel_st;
  lgfx::Bus_SPI       bus;
  lgfx::Light_PWM     light;
  lgfx::Touch_XPT2046 touch;

  LGFX() {
    {
      auto c = bus.config();
      c.spi_host = SPI2_HOST;
      c.spi_mode = 0;
      c.freq_write = 40000000;
      c.freq_read = 16000000;
      c.spi_3wire = false;
      c.use_lock = true;
      c.dma_channel = SPI_DMA_CH_AUTO;
      c.pin_sclk = 14; c.pin_mosi = 13; c.pin_miso = 12; c.pin_dc = 2;
      bus.config(c);
    }
    {
      auto c = light.config();
      c.pin_bl = 21; c.invert = false; c.freq = 12000; c.pwm_channel = 7;
      light.config(c);
    }
    {
      auto c = touch.config();
      c.x_min = 300; c.x_max = 3900; c.y_min = 200; c.y_max = 3700;
      c.pin_int = 36; c.bus_shared = false; c.offset_rotation = 0;
      c.spi_host = SPI3_HOST; c.freq = 1000000;
      c.pin_sclk = 25; c.pin_mosi = 32; c.pin_miso = 39; c.pin_cs = 33;
      touch.config(c);
    }
    setupPanel(panel_ili);
    setupPanel(panel_st);
    usePanel(false);
  }

  template <class P> void setupPanel(P& p) {
    auto c = p.config();
    c.pin_cs = 15; c.pin_rst = -1; c.pin_busy = -1;
    c.memory_width = 240; c.memory_height = 320;
    c.panel_width = 240; c.panel_height = 320;
    c.offset_x = 0; c.offset_y = 0; c.offset_rotation = 0;
    c.dummy_read_pixel = 8; c.dummy_read_bits = 1;
    c.readable = true; c.invert = false; c.rgb_order = false;
    c.dlen_16bit = false; c.bus_shared = false;
    p.config(c);
    p.setBus(&bus);
    p.setLight(&light);
    p.setTouch(&touch);
  }

  void usePanel(bool st7789) {
    if (st7789) setPanel(&panel_st); else setPanel(&panel_ili);
  }
};
