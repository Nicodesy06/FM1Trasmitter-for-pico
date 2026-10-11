#pragma once

// Board abstraction: everything that differs between RP2040 boards comes from
// the generated fm1t_board_config.h (cmake/fm1t_board.cmake, boards/<id>.json).
// The USB_KEY / PIO USB / JieLi code only sees PIN_DP, PIN_DM and rgb().

#include <stdbool.h>
#include <stdint.h>

#include "fm1t_board_config.h"

#define PIN_DP FM1T_PIN_DP
#define PIN_DM FM1T_PIN_DM

// Pico-PIO-USB is configured with PIO_USB_PINOUT_DPDM (D- = D+ + 1), and the
// usb_key program drives both lines as one `set pins` group of two.
_Static_assert(FM1T_PIN_DM == FM1T_PIN_DP + 1, "D- must be D+ + 1");
_Static_assert(FM1T_PIN_DM <= 29, "D+/D- must be RP2040 GPIOs");
_Static_assert((FM1T_RESERVED_MASK & ((1u << FM1T_PIN_DP) | (1u << FM1T_PIN_DM))) == 0,
               "D+/D- on a pin the board reserves");
_Static_assert(FM1T_RECOVERY_PIO != FM1T_HOST_PIO, "recovery and host need separate PIO blocks");
_Static_assert(FM1T_SYS_KHZ == 120000, "clk_sys is fixed at the validated 120 MHz");

#if LIB_PICO_STDIO_UART
#error "stdio UART would claim the FM-1 USB lines (GP0/GP1); the console is USB CDC"
#endif

#define FM1T_LED_NONE 0
#define FM1T_LED_MONO 1
#define FM1T_LED_RGB 2

#if FM1T_LED_KIND == FM1T_LED_RGB
_Static_assert(FM1T_LED_RED_PIN != PIN_DP && FM1T_LED_RED_PIN != PIN_DM &&
               FM1T_LED_GREEN_PIN != PIN_DP && FM1T_LED_GREEN_PIN != PIN_DM &&
               FM1T_LED_BLUE_PIN != PIN_DP && FM1T_LED_BLUE_PIN != PIN_DM,
               "LED on a USB line");
#elif FM1T_LED_KIND == FM1T_LED_MONO
_Static_assert(FM1T_LED_MONO_PIN != PIN_DP && FM1T_LED_MONO_PIN != PIN_DM, "LED on a USB line");
#endif

// Configures the profile's LED GPIOs (none: touches no pin) and switches them off.
void board_leds_init(void);

// Status colour. RGB boards show it as is; a single LED is on for any colour.
// LEDs are a convenience only: the console log is the reference.
void rgb(bool r, bool g, bool b);

// LED self-test (core 0): cycles the LEDs for a few seconds, then restores the
// last status colour. board_led_test_poll() runs from the core 0 loop.
void board_led_test_start(void);
void board_led_test_poll(void);
bool board_led_test_active(void);

const char *board_led_kind_name(void);
