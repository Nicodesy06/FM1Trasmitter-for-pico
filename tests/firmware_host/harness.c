// Runs the real src/transporter_proto.c and src/jieli_uboot.c on the host,
// with stdin/stdout standing in for the CDC 1 data port and mock_uboot.c for
// the FM-1 behind the PIO USB host. Core 0 (fm1_proto_rx / fm1_proto_drain) and
// core 1 (fm1_proto_poll) are interleaved in one thread.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "pico/stdlib.h"
#include "tusb.h"

#include "jieli_uboot.h"
#include "log.h"
#include "pio_host.h"
#include "selftest.h"
#include "transporter_proto.h"
#include "mock_uboot.h"

volatile uint8_t fm1t_phase = FM1T_PHASE_UBOOT;
volatile uint32_t fm1t_key_packets;

uint64_t time_us_64(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

void sleep_ms(uint32_t ms) {
    usleep(ms * 1000u);
}

// transporter_proto.c spins here while its 16 KiB TX ring is full, waiting for
// core 0 to drain it; in one thread, drain right here.
void tight_loop_contents(void) {
    fm1_proto_drain();
}

uint32_t tud_cdc_n_write(uint8_t itf, const void *buf, uint32_t n) {
    if (itf != 1) return 0;
    fwrite(buf, 1, n, stdout);
    return n;
}

uint32_t tud_cdc_n_write_str(uint8_t itf, const char *s) {
    return tud_cdc_n_write(itf, s, (uint32_t)strlen(s));
}

uint32_t tud_cdc_n_write_flush(uint8_t itf) {
    (void)itf;
    fflush(stdout);
    return 0;
}

bool tud_cdc_n_connected(uint8_t itf) {
    (void)itf;
    return true;
}

uint32_t tud_cdc_n_write_available(uint8_t itf) {
    (void)itf;
    return 64;
}

void dlog(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("[dlog] ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

uint32_t log_dropped(void) {
    return 0;
}

bool fm1_host_uboot_ready(void) {
    return mock_uboot_ready();
}

bool fm1_host_v15_mounted(void) {
    return false;
}

bool fm1_host_softkey(void) {
    return false;
}

bool fm1_host_bot(const uint8_t *cdb, uint8_t cdb_len, bool in, void *buf, uint16_t len,
                  uint32_t *got) {
    return mock_bot(cdb, cdb_len, in, buf, len, got);
}

bool fm1t_selftest_command(const char *line, uint8_t itf) {
    if (!strcmp(line, "ident")) {
        tud_cdc_n_write_str(itf, "OK fm1t=2 fw=host-harness build=test upstream=a632d92 "
                                 "board=host_harness soc=host dp=0 dm=1 leds=none "
                                 "binary=host validation=SIMULATED\n");
        tud_cdc_n_write_flush(itf);
        return true;
    }
    return false;
}

void fm1_rekey(void) {
    fflush(stdout);
    mock_finish();
    _exit(3);       // the real firmware reboots here
}

int main(void) {
    mock_init();
    setvbuf(stdout, NULL, _IOFBF, 1 << 16);
    char buf[512];
    for (;;) {
        ssize_t n = read(0, buf, sizeof(buf));
        if (n <= 0) break;
        fm1_proto_rx(buf, (unsigned)n);
        fm1_proto_poll();
        fm1_proto_drain();
        fflush(stdout);
    }
    mock_finish();
    return 0;
}
