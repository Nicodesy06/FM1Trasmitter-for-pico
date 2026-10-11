// FM-1 Transporter board header: generic Pico-compatible RP2040 (experimental).
// Generated values live in boards/generic_rp2040.json; keep the two in sync
// (tests/test_profiles.py checks it).
//
// Conservative on purpose: the flash chip and its size are not verified, so
// boot2 is the universal 03h-read variant, the image is linked for 2 MB and
// the firmware runs from RAM (copy_to_ram).

#ifndef _BOARDS_FM1T_GENERIC_RP2040_H
#define _BOARDS_FM1T_GENERIC_RP2040_H

pico_board_cmake_set(PICO_PLATFORM, rp2040)

#define FM1T_GENERIC_RP2040

// 12 MHz crystal assumed (the RP2040 USB boot ROM needs it, so a board that
// shows up as RPI-RP2 has one). Longer start-up margin, as on the XIAO.
#ifndef PICO_XOSC_STARTUP_DELAY_MULTIPLIER
#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64
#endif

// No default UART: GP0/GP1 belong to the FM-1 USB lines.


#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1

#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 4
#endif

pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (2 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
#endif

#ifndef PICO_RP2040_B0_SUPPORTED
#define PICO_RP2040_B0_SUPPORTED 1
#endif

#endif
