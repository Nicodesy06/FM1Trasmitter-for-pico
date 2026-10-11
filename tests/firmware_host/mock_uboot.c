#include "mock_uboot.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FLASH_SIZE (1024u * 1024u)
#define LOADER_ADDR 0x1C02000u
#define LOADER_SIZE 24064u
#define RAM_BASE 0x1C00000u
#define RAM_SIZE 0x40000u

static uint8_t flash[FLASH_SIZE];
static uint8_t ram[RAM_SIZE];
static uint32_t loader_bytes;
static bool loader_running;
static bool uboot = true;
static uint16_t chip_key = 0x980F;
static uint32_t flash_id = 0x856014;
static long flaky_read, fail_read, reads;
static FILE *cmdlog;

static uint16_t crc16(const uint8_t *d, uint32_t n, uint16_t crc) {
    for (uint32_t i = 0; i < n; i++) {
        crc ^= (uint16_t)d[i] << 8;
        for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

// Independent re-implementation from docs/PROTOCOL.md (key 0xFFFFFFFF).
static void cipher(uint8_t *buf, uint32_t n) {
    static const uint8_t magic[16] = {0xC3, 0xCF, 0xC0, 0xE8, 0xCE, 0xD2, 0xB0, 0xAE,
                                      0xC4, 0xE3, 0xA3, 0xAC, 0xD3, 0xF1, 0xC1, 0xD6};
    static const uint8_t ff[2] = {0xFF, 0xFF};
    uint16_t crc = crc16(ff, 2, 0xFFFF);
    for (uint32_t i = 0; i < n; i++) {
        crc = crc16(&magic[i % 16], 1, crc);
        buf[i] ^= (uint8_t)crc;
    }
}

static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static void logcmd(const char *fmt, uint32_t a, uint32_t b) {
    if (!cmdlog) return;
    fprintf(cmdlog, fmt, a, b);
    fputc('\n', cmdlog);
    fflush(cmdlog);
}

void mock_init(void) {
    const char *e;
    for (uint32_t i = 0; i < FLASH_SIZE; i++) flash[i] = (uint8_t)((i * 2654435761u) >> 24);
    if ((e = getenv("MOCK_FLASH")) && *e) {
        FILE *f = fopen(e, "rb");
        if (!f || fread(flash, 1, FLASH_SIZE, f) != FLASH_SIZE) {
            fprintf(stderr, "mock: cannot read 1 MiB from %s\n", e);
            exit(2);
        }
        fclose(f);
    }
    if ((e = getenv("MOCK_UBOOT"))) uboot = atoi(e) != 0;
    if ((e = getenv("MOCK_CHIP_KEY"))) chip_key = (uint16_t)strtoul(e, NULL, 16);
    if ((e = getenv("MOCK_FLASH_ID"))) flash_id = (uint32_t)strtoul(e, NULL, 16);
    if ((e = getenv("MOCK_FLAKY_READ"))) flaky_read = atol(e);
    if ((e = getenv("MOCK_FAIL_READ"))) fail_read = atol(e);
    if ((e = getenv("MOCK_LOG")) && *e) cmdlog = fopen(e, "a");
}

void mock_finish(void) {
    const char *e = getenv("MOCK_FLASH_OUT");
    if (e && *e) {
        FILE *f = fopen(e, "wb");
        if (f) {
            fwrite(flash, 1, FLASH_SIZE, f);
            fclose(f);
        }
    }
    if (cmdlog) fclose(cmdlog);
}

bool mock_uboot_ready(void) {
    return uboot;
}

static void echo(uint8_t *r, uint16_t cmd) {
    memset(r, 0, 16);
    r[0] = (uint8_t)(cmd >> 8);
    r[1] = (uint8_t)cmd;
}

bool mock_bot(const uint8_t *cdb, uint8_t cdb_len, bool in, void *vbuf, uint16_t len,
              uint32_t *got) {
    uint8_t *buf = vbuf;
    if (!uboot || cdb_len != 16) return false;
    uint16_t cmd = (uint16_t)(cdb[0] << 8 | cdb[1]);
    const uint8_t *a = &cdb[2];
    if (got) *got = 0;

    switch (cmd) {
    case 0xFB06: {      // WRITE_MEMORY (ROM): addr, len, 00, crc16 LE
        uint32_t addr = be32(a);
        uint16_t n = (uint16_t)(a[4] << 8 | a[5]);
        uint16_t crc = (uint16_t)(a[7] | a[8] << 8);
        logcmd("WRITE_MEMORY %08X %u", addr, n);
        if (loader_running || in || n != len || crc16(buf, n, 0) != crc) return false;
        if (addr < RAM_BASE || addr + n > RAM_BASE + RAM_SIZE) return false;
        memcpy(&ram[addr - RAM_BASE], buf, n);
        cipher(&ram[addr - RAM_BASE], n);
        if (addr >= LOADER_ADDR && addr < LOADER_ADDR + LOADER_SIZE) loader_bytes += n;
        return true;
    }
    case 0xFD07: {      // READ_MEMORY (ROM)
        uint32_t addr = be32(a);
        uint16_t n = (uint16_t)(a[4] << 8 | a[5]);
        logcmd("READ_MEMORY %08X %u", addr, n);
        if (loader_running || !in || n != len) return false;
        if (addr < RAM_BASE || addr + n > RAM_BASE + RAM_SIZE) return false;
        memcpy(buf, &ram[addr - RAM_BASE], n);
        cipher(buf, n);
        if (got) *got = n;
        return true;
    }
    case 0xFB08: {      // JUMP
        uint32_t addr = be32(a);
        logcmd("JUMP %08X %u", addr, (uint32_t)(a[4] << 8 | a[5]));
        if (loader_running || !in || len != 16) return false;
        echo(buf, cmd);
        if (addr == LOADER_ADDR) {
            if (loader_bytes < LOADER_SIZE) return false;
            loader_running = true;
        }
        if (got) *got = 16;
        return true;
    }
    }

    if (!loader_running) {
        logcmd("REFUSED %04X %u", cmd, 0);
        return false;
    }

    switch (cmd) {
    case 0xFC09: {      // READ_KEY
        logcmd("READ_KEY %08X %u", be32(a), 0);
        if (!in || len != 16) return false;
        echo(buf, cmd);
        uint8_t k[2] = {0, 0};
        cipher(k, 2);                    // keystream bytes 0,1
        buf[2 + 5] = (uint8_t)(chip_key & 0xFF) ^ k[0];
        buf[2 + 4] = (uint8_t)(chip_key >> 8) ^ k[1];
        if (got) *got = 16;
        return true;
    }
    case 0xFC0A:        // GET_ONLINE_DEVICE
        logcmd("GET_ONLINE_DEVICE %u %u", 0, 0);
        if (!in || len != 16) return false;
        echo(buf, cmd);
        buf[2] = 3;
        buf[4] = (uint8_t)flash_id;
        buf[5] = (uint8_t)(flash_id >> 8);
        buf[6] = (uint8_t)(flash_id >> 16);
        if (got) *got = 16;
        return true;
    case 0xFC14:        // GET_USB_BUFF_SIZE
        logcmd("GET_USB_BUFF_SIZE %u %u", 0, 0);
        if (!in || len != 16) return false;
        echo(buf, cmd);
        buf[4] = 0x10;                   // 0x00001000 = 4096
        if (got) *got = 16;
        return true;
    case 0xFD05: {      // READ_FLASH
        uint32_t addr = be32(a);
        uint16_t n = (uint16_t)(a[4] << 8 | a[5]);
        reads++;
        logcmd("READ_FLASH %06X %u", addr, n);
        if (!in || n != len || addr + n > FLASH_SIZE) return false;
        if (fail_read && reads >= fail_read && reads < fail_read + 3) return false;
        memcpy(buf, &flash[addr], n);
        if (flaky_read && reads == flaky_read) buf[n / 2] ^= 0x10;
        if (got) *got = n;
        return true;
    }
    case 0xFB01: {      // ERASE_SECTOR
        uint32_t addr = be32(a);
        logcmd("ERASE_SECTOR %06X %u", addr, 0);
        if (!in || len != 16 || addr % 4096 || addr >= FLASH_SIZE) return false;
        memset(&flash[addr], 0xFF, 4096);
        echo(buf, cmd);
        if (got) *got = 16;
        return true;
    }
    case 0xFB04: {      // WRITE_FLASH (NOR: bits only cleared)
        uint32_t addr = be32(a);
        uint16_t n = (uint16_t)(a[4] << 8 | a[5]);
        uint16_t crc = (uint16_t)(a[7] | a[8] << 8);
        logcmd("WRITE_FLASH %06X %u", addr, n);
        if (in || n != len || addr + n > FLASH_SIZE || crc16(buf, n, 0) != crc) return false;
        for (uint16_t i = 0; i < n; i++) flash[addr + i] &= buf[i];
        return true;
    }
    case 0xFC0C:        // RUN_APP
        logcmd("RUN_APP %u %u", 0, 0);
        loader_running = false;
        uboot = false;
        return false;                    // the chip resets before answering
    default:
        logcmd("UNKNOWN %04X %u", cmd, 0);
        return false;
    }
}
