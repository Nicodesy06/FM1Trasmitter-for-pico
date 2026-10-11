#pragma once

// Board identification and self-test commands on the data port (CDC 1).
//
// Answered on core 0, immediately, like `ping`: they must work while core 1
// is still sending the USB_KEY with no FM-1 attached. None of them touches
// the FM-1 protocol, and none writes anything anywhere.
//
//   ident    -> OK fm1t=2 fw=<version> build=<id> upstream=<rev> board=<id> soc=RP2040
//               dp=<gpio> dm=<gpio> leds=<none|mono|rgb> binary=<type> validation=<level>
//   hwinfo   -> OK clk_sys=<hz> chip_rev=<n> flash_jedec=<hex|none> flash_vendor=<name>
//               flash_sfdp_bytes=<n> flash_jedec_bytes=<n> flash_build_bytes=<n>
//               flash_declared_bytes=<n> reset=<por|watchdog|rekey|reboot-cmd|reattach> uid=<hex>
//   diag     -> OK phase=<name> uptime_ms=<n> key_packets=<n> log_dropped=<n>
//               dp_func=<n> dm_func=<n> dp_in=<0|1> dm_in=<0|1> uboot=<0|1>
//   piotest  -> OK phase=<name> samples=<n> dp_edges=<n> dm_edges=<n> dp_high=<n> dm_high=<n>
//               (samples the GP pads' input buffers for 30 ms: proves the key
//               state machine really toggles D+/D- at the pads, nothing more)
//   ledtest  -> OK leds=<kind> (cycles the LEDs for 4 s; the user must look)
//   reboot   -> OK reboot (watchdog reboot; comes back with reset=reboot-cmd)
//   bootsel  -> OK bootsel (reboots into the RP2040 USB boot ROM, RPI-RP2 disk)

#include <stdbool.h>
#include <stdint.h>

enum fm1t_phase {
    FM1T_PHASE_BOOT,
    FM1T_PHASE_WAIT_CONSOLE,
    FM1T_PHASE_KEYING,          // USB_KEY on PIO0, waiting for the FM-1 ROM
    FM1T_PHASE_PULSES,          // ROM acknowledged, 1 ms keep-alive pulses
    FM1T_PHASE_HOST,            // Pico-PIO-USB host running, nothing mounted
    FM1T_PHASE_MOUNTED,
    FM1T_PHASE_UBOOT,           // UBOOT answered INQUIRY
};

extern volatile uint8_t fm1t_phase;
extern volatile uint32_t fm1t_key_packets;

// Call first thing in main(): records the reset reason before anyone clears
// the watchdog scratch registers.
void fm1t_selftest_init(void);

// Core 0. Returns true if `line` was a self-test command (reply already sent
// on `itf`).
bool fm1t_selftest_command(const char *line, uint8_t itf);

const char *fm1t_phase_name(uint8_t phase);
const char *fm1t_reset_reason(void);
