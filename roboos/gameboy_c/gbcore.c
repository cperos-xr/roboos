/* gbcore.c: a small C wrapper around Peanut-GB for robotd (loaded with ctypes by robotd/gameboy.py).
 *
 * peanut_gb.h is Peanut-GB by Mahyar Koshkouei (MIT License), copied unchanged from
 * https://github.com/deltabeard/Peanut-GB at commit d0bcca771c83a2638c93a9ae61f3d51f226dc905.
 *
 * robotd compiles this file on the robot the first time a game starts:
 *   cc -O2 -shared -fPIC -o data/gameboy/gbcore.so robotd/gameboy_c/gbcore.c
 * Original Game Boy only (no Game Boy Color), no sound yet.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define ENABLE_SOUND 0
#define ENABLE_LCD 1
#include "peanut_gb.h"

#define GBC_W 160
#define GBC_H 144
#define GBC_FATAL (GB_INVALID_OPCODE + 1)     /* the only error that stops the game */

typedef struct {
	struct gb_s gb;
	uint8_t *rom;
	size_t rom_len;
	uint8_t *ram;                 /* the cartridge's battery-backed memory (the save) */
	size_t ram_len;
	int dirty;                    /* the game wrote to its save since the last gbc_take_dirty() */
	int err;                      /* first emulation error (enum gb_error_e + 1), 0 = none */
	uint16_t err_addr;
	uint8_t fb[GBC_W * GBC_H];    /* one byte per pixel: shade 0 (lightest) .. 3 (darkest) */
	uint8_t sent[GBC_W * GBC_H];  /* what the screen shows (gbc_delta) */
	uint8_t row_ok[GBC_H];        /* 1 = that row of sent[] is really on the screen */
} core_t;

static uint8_t rom_read(struct gb_s *gb, const uint_fast32_t addr)
{
	core_t *c = gb->direct.priv;
	return addr < c->rom_len ? c->rom[addr] : 0xFF;
}

static uint8_t ram_read(struct gb_s *gb, const uint_fast32_t addr)
{
	core_t *c = gb->direct.priv;
	return addr < c->ram_len ? c->ram[addr] : 0xFF;
}

static void ram_write(struct gb_s *gb, const uint_fast32_t addr, const uint8_t val)
{
	core_t *c = gb->direct.priv;
	if(addr < c->ram_len && c->ram[addr] != val) {
		c->ram[addr] = val;
		c->dirty = 1;
	}
}

static void on_error(struct gb_s *gb, const enum gb_error_e e, const uint16_t addr)
{
	core_t *c = gb->direct.priv;
	if(!c->err || (int)e + 1 == GBC_FATAL) {
		c->err = (int)e + 1;
		c->err_addr = addr;
	}
}

static void draw_line(struct gb_s *gb, const uint8_t *pixels, const uint_fast8_t line)
{
	core_t *c = gb->direct.priv;
	if(line >= GBC_H)
		return;
	uint8_t *row = c->fb + (size_t)line * GBC_W;
	for(int x = 0; x < GBC_W; x++)
		row[x] = pixels[x] & 3;          /* the low 2 bits are the shade after the game's palette */
}

/* Start a game. err: 0 ok, 1 cartridge type not supported, 2 bad header checksum, 100 out of memory. */
core_t *gbc_new(const uint8_t *rom, size_t len, int *err)
{
	core_t *c = calloc(1, sizeof *c);
	if(!c || !(c->rom = malloc(len))) {
		free(c);
		*err = 100;
		return NULL;
	}
	memcpy(c->rom, rom, len);
	c->rom_len = len;
	enum gb_init_error_e e = gb_init(&c->gb, rom_read, ram_read, ram_write, on_error, c);
	if(e != GB_INIT_NO_ERROR) {
		free(c->rom);
		free(c);
		*err = (int)e;
		return NULL;
	}
	size_t n = 0;
	if(gb_get_save_size_s(&c->gb, &n) == 0 && n) {
		if(!(c->ram = calloc(1, n))) {
			free(c->rom);
			free(c);
			*err = 100;
			return NULL;
		}
		c->ram_len = n;
	}
	gb_init_lcd(&c->gb, draw_line);
	time_t now = time(NULL);
	struct tm tm;
	localtime_r(&now, &tm);
	gb_set_rtc(&c->gb, &tm);              /* games with a clock (MBC3) start at the robot's local time */
	*err = 0;
	return c;
}

void gbc_free(core_t *c)
{
	if(!c)
		return;
	free(c->rom);
	free(c->ram);
	free(c);
}

/* Run one frame (1/59.73 s of game time). Returns 0, or GBC_FATAL when the game hit an invalid instruction. */
int gbc_run_frame(core_t *c)
{
	if(c->err == GBC_FATAL)
		return c->err;
	gb_run_frame(&c->gb);
	return c->err == GBC_FATAL ? c->err : 0;
}

/* pressed: bit set = button held (JOYPAD_A 0x01, B 0x02, SELECT 0x04, START 0x08, RIGHT 0x10, LEFT 0x20, UP 0x40, DOWN 0x80) */
void gbc_set_buttons(core_t *c, uint8_t pressed)
{
	c->gb.direct.joypad = (uint8_t)~pressed;   /* Peanut-GB: 0 = pressed */
}

const uint8_t *gbc_frame(core_t *c) { return c->fb; }
size_t gbc_save_size(core_t *c) { return c->ram_len; }
const uint8_t *gbc_save_data(core_t *c) { return c->ram; }

void gbc_load_save(core_t *c, const uint8_t *data, size_t len)
{
	if(c->ram)
		memcpy(c->ram, data, len < c->ram_len ? len : c->ram_len);
}

int gbc_take_dirty(core_t *c)
{
	int d = c->dirty;
	c->dirty = 0;
	return d;
}

void gbc_reset(core_t *c)
{
	gb_reset(&c->gb);
	c->err = 0;
}

/* For the screen's "gbf" message (firmware/cyd_dashboard/media.ino gbFrame): encode the rows of frame (160 x 144
 * shades, a copy taken after a frame) that differ from what the screen already shows. One record per row:
 * [row][n][n bytes]. n == 40: 2-bit pixels, 4 per byte, leftmost pixel in the top two bits; n < 40: (count, byte)
 * pairs that expand to those 40 bytes (flat rows such as sky shrink to a few bytes). Rows that do not fit in cap wait
 * for the next call. Returns the number of bytes written. */
size_t gbc_delta(core_t *c, const uint8_t *frame, uint8_t *out, size_t cap)
{
	enum { RB = GBC_W / 4 };
	size_t o = 0;
	for(int y = 0; y < GBC_H; y++) {
		const uint8_t *row = frame + (size_t)y * GBC_W;
		uint8_t *was = c->sent + (size_t)y * GBC_W;
		if(c->row_ok[y] && !memcmp(row, was, GBC_W))
			continue;
		uint8_t packed[RB], rle[RB];
		for(int b = 0; b < RB; b++)
			packed[b] = (uint8_t)((row[b * 4] & 3) << 6 | (row[b * 4 + 1] & 3) << 4 |
					      (row[b * 4 + 2] & 3) << 2 | (row[b * 4 + 3] & 3));
		size_t n = 0;
		int b = 0;
		while(b < RB && n + 2 < RB) {            /* stop as soon as RLE would not be shorter than raw */
			int run = 1;
			while(b + run < RB && packed[b + run] == packed[b])
				run++;
			rle[n++] = (uint8_t)run;
			rle[n++] = packed[b];
			b += run;
		}
		const int use_rle = (b == RB);
		const size_t len = use_rle ? n : RB;
		if(o + 2 + len > cap)
			break;
		out[o++] = (uint8_t)y;
		out[o++] = (uint8_t)len;
		memcpy(out + o, use_rle ? rle : packed, len);
		o += len;
		memcpy(was, row, GBC_W);
		c->row_ok[y] = 1;
	}
	return o;
}

/* The screen shows something else now (a JPEG, a dialog...): the next gbc_delta sends every row. */
void gbc_delta_reset(core_t *c) { memset(c->row_ok, 0, sizeof c->row_ok); }

/* Read n bytes of the Game Boy's memory map from addr (for the AI player: position, map, party HP...). */
void gbc_peek(core_t *c, uint16_t addr, uint8_t *out, size_t n)
{
	for(size_t i = 0; i < n; i++)
		out[i] = __gb_read(&c->gb, (uint16_t)(addr + i));
}

int gbc_error(core_t *c, int *addr)
{
	if(addr)
		*addr = c->err_addr;
	return c->err;
}
