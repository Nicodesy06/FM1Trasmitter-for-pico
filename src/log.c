#include "log.h"

#include <stdarg.h>
#include <stdio.h>

#include "pico/stdlib.h"
#include "pico/stdio/driver.h"
#include "hardware/sync.h"
#include "tusb.h"

#define LOG_BUF_SIZE 8192   // power of two

static char buf[LOG_BUF_SIZE];
static volatile uint32_t head;  // written by producers (under the stdio mutex)
static volatile uint32_t tail;  // written by core 0 only
static volatile uint32_t dropped;
static uint64_t t0;

static void log_out_chars(const char *s, int len) {
    uint32_t h = head;
    for (int i = 0; i < len; i++) {
        if (h - tail >= LOG_BUF_SIZE) {
            dropped++;
            continue;
        }
        buf[h & (LOG_BUF_SIZE - 1)] = s[i];
        h++;
    }
    __dmb();
    head = h;
}

static stdio_driver_t log_driver = {
    .out_chars = log_out_chars,
#if PICO_STDIO_ENABLE_CRLF_SUPPORT
    .crlf_enabled = PICO_STDIO_DEFAULT_CRLF,
#endif
};

void log_init(void) {
    t0 = time_us_64();
    stdio_set_driver_enabled(&log_driver, true);
}

uint32_t log_dropped(void) {
    return dropped;
}

void log_drain(void) {
    if (!tud_cdc_n_connected(0)) {
        // Keep the newest output; nobody is listening yet.
        uint32_t h = head;
        if (h - tail > LOG_BUF_SIZE / 2) tail = h - LOG_BUF_SIZE / 2;
        return;
    }

    for (;;) {
        uint32_t h = head;
        __dmb();
        uint32_t t = tail;
        if (h == t) break;

        uint32_t avail = tud_cdc_n_write_available(0);
        if (!avail) break;

        uint32_t idx = t & (LOG_BUF_SIZE - 1);
        uint32_t n = h - t;
        if (n > LOG_BUF_SIZE - idx) n = LOG_BUF_SIZE - idx;
        if (n > avail) n = avail;

        n = tud_cdc_n_write(0, &buf[idx], n);
        tail = t + n;
    }

    tud_cdc_n_write_flush(0);
}

void dlog(const char *fmt, ...) {
    // Format the whole line first so lines from the two cores never interleave.
    char line[256];
    int n = snprintf(line, sizeof(line), "[%9llu us] ",
                     (unsigned long long)(time_us_64() - t0));
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line + n, sizeof(line) - (size_t)n, fmt, ap);
    va_end(ap);
    puts(line);
}
