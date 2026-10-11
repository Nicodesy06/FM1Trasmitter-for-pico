// FM-1 Transporter - USB_KEY recovery + PIO USB host for the JieLi UBOOT
// Seeed XIAO RP2040: GP0/D6 -> FM-1 D+, GP1/D7 -> FM-1 D-, GND -> GND.
// FM-1 VBUS is not connected in the current battery-powered prototype.
//
// core 0: native USB device (Mac-facing CDC console) and log output
// core 1: USB_KEY recovery on PIO0 -> handoff -> Pico-PIO-USB host on PIO1
//
// GP0/GP1 have exactly one owner at a time, and both owners live on core 1:
// recovery hands the bus to the host in one call sequence, with the ROM
// pulses running until the moment the host takes the pins.

#include <stdio.h>

#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "hardware/clocks.h"
#include "hardware/watchdog.h"
#include "tusb.h"

#include "log.h"
#include "pio_host.h"
#include "recovery.h"
#include "transporter_proto.h"

#define CONSOLE_WAIT_MS 10000
#define ATTACHED_PULSE_MS 6000      // as in the runs where V15 enumerated
#define REKEY_MAGIC 0x4B455931      // "KEY1" in watchdog scratch 0: force USB_KEY

// Reboots the transporter straight into USB_KEY mode even if D+ is pulled up,
// for a unit stuck with its USB up. The FM-1 then needs one power cycle.
void fm1_rekey(void) {
    watchdog_hw->scratch[0] = REKEY_MAGIC;
    watchdog_reboot(0, 0, 50);
}

static volatile bool console_ready;

static void core1_main(void) {
    for (int i = 0; i < CONSOLE_WAIT_MS / 100 && !console_ready; i++) {
        sleep_ms(100);
    }
    sleep_ms(200);

    printf("\nFM-1 Transporter\n");
    printf("XIAO RP2040: D+=GP0/D6 D-=GP1/D7, PIO host on PIO1\n");
    printf("If the FM-1 runs stock V15, leave it on: fm1t enters UBOOT via the soft key.\n");
    printf("Otherwise switch it OFF, and ON again once the key is running.\n");

    recovery_init();
#if FM1T_HOST_ONLY
    // Host bring-up without the key path: attach any full-speed device, or an
    // FM-1 already in UBOOT (e.g. via the V15 USB-MIDI soft key).
    dlog("HOST-ONLY build: skipping USB_KEY recovery");
#else
    bool force_key = watchdog_hw->scratch[0] == REKEY_MAGIC;
    watchdog_hw->scratch[0] = 0;
    if (force_key) {
        // A loader or UBOOT may still hold D+ for a moment after we stop
        // talking to it; keying into that would mistake it for a fresh ROM.
        dlog("REKEY: waiting for D+ to drop (loader watchdog / power-off)");
        bool dropped = recovery_wait_detached(15000);
        dlog("REKEY: %s; USB_KEY forced now",
             dropped ? "D+ dropped" : "D+ still high after 15 s - power-cycle the FM-1");
        recovery_run();
    } else if (recovery_target_attached()) {
        dlog("D+ already pulled up (FM-1 app or a waiting UBOOT) - skipping USB_KEY");
        recovery_pulses_only(ATTACHED_PULSE_MS);
    } else {
        recovery_run();
    }
#endif

    // The ROM is holding D+ up and our pulses are still running. Swap owners
    // with the smallest possible gap.
    uint64_t t_release = time_us_64();
    recovery_release_bus();
    fm1_pio_host_start();
    uint64_t t_host = time_us_64();

    rgb(true, true, true);
    dlog("HANDOFF: pulses stopped, PIO host started in %llu us",
         (unsigned long long)(t_host - t_release));

    for (;;) {
        fm1_pio_host_task();
    }
}

// Console (CDC 0): one-letter diagnostic commands, each on its own line.
// Anything longer is answered with the help text by fm1_pio_host_command().
static void console_rx(const char *buf, uint32_t n) {
    static char line[8];
    static uint32_t len;
    for (uint32_t i = 0; i < n; i++) {
        char c = buf[i];
        if (c == '\r' || c == '\n') {
            if (len) fm1_pio_host_command(len == 1 ? line[0] : '?');
            len = 0;
        } else if (len < sizeof(line)) {
            line[len++] = c;
        }
    }
}

void tud_cdc_rx_cb(uint8_t itf) {
    char buf[64];
    uint32_t n = tud_cdc_n_read(itf, buf, sizeof(buf));
    if (itf == 0) {
        console_rx(buf, n);
    } else {
        fm1_proto_rx(buf, n);
    }
}

int main(void) {
    // Pico-PIO-USB needs clk_sys to be a multiple of 12 MHz. The recovery
    // PIO dividers are derived from clk_sys, so they follow automatically.
    set_sys_clock_khz(120000, true);

    log_init();
    tud_init(0);

    multicore_launch_core1(core1_main);

    for (;;) {
        tud_task();
        console_ready = tud_cdc_n_connected(0);
        log_drain();
        fm1_proto_drain();
    }
}
