#include "selftest.h"

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/bootrom.h"
#include "pico/unique_id.h"
#include "pico/binary_info.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/watchdog.h"
#include "tusb.h"

#include "board.h"
#include "flash_info.h"
#include "flash_sfdp.h"
#include "log.h"
#include "pio_host.h"

#define REKEY_MAGIC 0x4B455931      // main.c, watchdog scratch 0
#define REBOOT_MAGIC 0x52425431     // "RBT1", watchdog scratch 2
#define REATTACH_SCRATCH 1          // pio_host.c
#define PIOTEST_US 30000
#define STR_(x) #x
#define STR(x) STR_(x)

volatile uint8_t fm1t_phase = FM1T_PHASE_BOOT;
volatile uint32_t fm1t_key_packets;

static const char *reset_reason = "por";

// Searched for by the host tools in a UF2 before flashing it: the image must
// carry exactly one marker, for the board the user selected.
__attribute__((used)) static const char board_marker[] =
    "FM1T-BOARD:" FM1T_BOARD_ID ";FM1T-BUILD:" FM1T_BUILD_ID ";";

bi_decl(bi_program_feature("FM1T board " FM1T_BOARD_ID))
bi_decl(bi_program_feature("FM-1 USB D+ GP" STR(FM1T_PIN_DP) " D- GP" STR(FM1T_PIN_DM)))
bi_decl(bi_2pins_with_names(FM1T_PIN_DP, "FM-1 USB D+", FM1T_PIN_DM, "FM-1 USB D-"))

void fm1t_selftest_init(void) {
    if (watchdog_caused_reboot()) {
        if (watchdog_hw->scratch[0] == REKEY_MAGIC) reset_reason = "rekey";
        else if (watchdog_hw->scratch[2] == REBOOT_MAGIC) reset_reason = "reboot-cmd";
        else if (watchdog_hw->scratch[REATTACH_SCRATCH]) reset_reason = "reattach";
        else reset_reason = "watchdog";
    }
    watchdog_hw->scratch[2] = 0;
}

const char *fm1t_reset_reason(void) {
    return reset_reason;
}

const char *fm1t_phase_name(uint8_t phase) {
    static const char *const names[] = {"boot", "wait-console", "keying", "pulses", "host",
                                        "mounted", "uboot"};
    return phase < sizeof(names) / sizeof(names[0]) ? names[phase] : "unknown";
}

static void reply(uint8_t itf, const char *s) {
    tud_cdc_n_write_str(itf, s);
    tud_cdc_n_write_str(itf, "\n");
    tud_cdc_n_write_flush(itf);
}

static void validation_token(char *out, size_t n) {
    snprintf(out, n, "%s", FM1T_BOARD_VALIDATION);
    for (char *p = out; *p; p++) {
        if (*p == ' ') *p = '_';
    }
}

static void cmd_ident(uint8_t itf) {
    char v[32], line[256];
    validation_token(v, sizeof(v));
    (void)board_marker;
    snprintf(line, sizeof(line),
             "OK fm1t=2 fw=%s build=%s upstream=%s board=%s soc=RP2040 dp=%d dm=%d leds=%s "
             "binary=%s validation=%s",
             FM1T_FW_VERSION, FM1T_BUILD_ID, FM1T_UPSTREAM, FM1T_BOARD_ID, FM1T_PIN_DP,
             FM1T_PIN_DM, board_led_kind_name(), FM1T_BINARY_TYPE, v);
    reply(itf, line);
}

static void cmd_hwinfo(uint8_t itf) {
    const fm1t_flash_info_t *f = fm1t_flash_info();
    char jedec[8] = "none";
    if (f->jedec_ok) snprintf(jedec, sizeof(jedec), "%02X%02X%02X", f->jedec[0], f->jedec[1], f->jedec[2]);
    char uid[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
    pico_get_unique_board_id_string(uid, sizeof(uid));
    char line[320];
    snprintf(line, sizeof(line),
             "OK clk_sys=%lu chip_rev=%u flash_jedec=%s flash_vendor=%s flash_sfdp_bytes=%llu "
             "flash_jedec_bytes=%lu flash_build_bytes=%lu flash_declared_bytes=%lu reset=%s uid=%s",
             (unsigned long)clock_get_hz(clk_sys), (unsigned)rp2040_chip_version(), jedec,
             f->jedec_ok ? fm1t_jedec_vendor(f->jedec[0]) : "none",
             (unsigned long long)f->sfdp_bytes, (unsigned long)f->jedec_bytes,
             (unsigned long)FM1T_FLASH_BUILD_BYTES, (unsigned long)FM1T_FLASH_DECLARED_BYTES,
             reset_reason, uid);
    reply(itf, line);
}

static void cmd_diag(uint8_t itf) {
    char line[256];
    snprintf(line, sizeof(line),
             "OK phase=%s uptime_ms=%lu key_packets=%lu log_dropped=%lu dp_func=%u dm_func=%u "
             "dp_in=%d dm_in=%d uboot=%d",
             fm1t_phase_name(fm1t_phase), (unsigned long)(time_us_64() / 1000),
             (unsigned long)fm1t_key_packets, (unsigned long)log_dropped(),
             (unsigned)gpio_get_function(PIN_DP), (unsigned)gpio_get_function(PIN_DM),
             gpio_get(PIN_DP), gpio_get(PIN_DM), fm1_host_uboot_ready());
    reply(itf, line);
}

// Reads the pads only (gpio_get), never changes their function or direction.
static void cmd_piotest(uint8_t itf) {
    uint8_t phase = fm1t_phase;
    uint32_t samples = 0, dp_edges = 0, dm_edges = 0, dp_high = 0, dm_high = 0;
    bool dp_prev = gpio_get(PIN_DP), dm_prev = gpio_get(PIN_DM);
    uint64_t end = time_us_64() + PIOTEST_US;
    while (time_us_64() < end) {
        uint32_t all = gpio_get_all();
        bool dp = (all >> PIN_DP) & 1, dm = (all >> PIN_DM) & 1;
        dp_edges += dp != dp_prev;
        dm_edges += dm != dm_prev;
        dp_high += dp;
        dm_high += dm;
        dp_prev = dp;
        dm_prev = dm;
        samples++;
    }
    char line[200];
    snprintf(line, sizeof(line),
             "OK phase=%s samples=%lu dp_edges=%lu dm_edges=%lu dp_high=%lu dm_high=%lu",
             fm1t_phase_name(phase), (unsigned long)samples, (unsigned long)dp_edges,
             (unsigned long)dm_edges, (unsigned long)dp_high, (unsigned long)dm_high);
    reply(itf, line);
}

bool fm1t_selftest_command(const char *line, uint8_t itf) {
    if (!strcmp(line, "ident")) {
        cmd_ident(itf);
    } else if (!strcmp(line, "hwinfo")) {
        cmd_hwinfo(itf);
    } else if (!strcmp(line, "diag")) {
        cmd_diag(itf);
    } else if (!strcmp(line, "piotest")) {
        cmd_piotest(itf);
    } else if (!strcmp(line, "ledtest")) {
        board_led_test_start();
        char r[32];
        snprintf(r, sizeof(r), "OK leds=%s", board_led_kind_name());
        reply(itf, r);
    } else if (!strcmp(line, "reboot")) {
        reply(itf, "OK reboot");
        dlog("SELFTEST: reboot requested on the data port");
        watchdog_hw->scratch[2] = REBOOT_MAGIC;
        watchdog_reboot(0, 0, 200);
    } else if (!strcmp(line, "bootsel")) {
        reply(itf, "OK bootsel");
        sleep_ms(50);
        rom_reset_usb_boot_extra(-1, 0, false);
    } else {
        return false;
    }
    return true;
}
