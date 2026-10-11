#pragma once

#include <stdbool.h>
#include <stdint.h>

// Seeed XIAO RP2040: GP0/D6 -> FM-1 D+, GP1/D7 -> FM-1 D-, GND -> GND.
#define PIN_DP 0
#define PIN_DM 1

// Claims PIO0 state machines and leaves the target bus released.
void recovery_init(void);

// True if something already pulls D+ up at boot: a running FM-1 application,
// or a ROM UBOOT left waiting. The key is pointless then; go straight to host.
bool recovery_target_attached(void);

// Waits until nothing pulls D+ up (e.g. the loader's watchdog reset fired).
bool recovery_wait_detached(uint32_t timeout_ms);

// Feeds the 1 ms pulses for ms without keying, then returns with them still
// running. Stock V15 also failed its first SETUP without them; with 6 s of
// pulses before the host started it enumerated every time.
void recovery_pulses_only(uint32_t ms);

// Sends the JieLi USB_KEY until the ROM acknowledges, then keeps feeding
// 1 ms pulses. Returns with the pulses STILL RUNNING: the ROM gives up if
// they stop before a USB host takes over.
void recovery_run(void);

// Stops every PIO0 state machine, returns GP0/GP1 to SIO inputs and clears
// any pad overrides. Must be called immediately before the PIO USB host
// takes the pins.
void recovery_release_bus(void);

void rgb(bool r, bool g, bool b);
