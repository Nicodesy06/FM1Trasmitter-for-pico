#pragma once

#include <stdbool.h>
#include <stdint.h>

// FM-1-facing PIO USB host (TinyUSB rhport 1, Pico-PIO-USB on PIO1,
// GP0 = D+, GP1 = D-). Runs entirely on core 1, after recovery.
//
// M0 scope: enumerate the JieLi ROM UBOOT, log its descriptors and run a
// read-only SCSI INQUIRY over Bulk-Only Transport. No memory or flash
// commands.

// Takes GP0/GP1. Call immediately after recovery_release_bus(); the gap
// between the last ROM pulse and this call should be as short as possible.
void fm1_pio_host_start(void);

// Services the host stack. Call continuously on core 1.
void fm1_pio_host_task(void);

bool fm1_pio_host_mounted(void);

// True once the mounted device answered INQUIRY as the JieLi UBOOT.
bool fm1_host_uboot_ready(void);

// True while the stock FM-1 application (4C4A:C755) is mounted.
bool fm1_host_v15_mounted(void);

// Sends the USB-MIDI soft key that makes stock V15 jump into the mask ROM
// UBOOT1.00 (the unit then re-attaches as 4C4A:8057). Core 1 only.
bool fm1_host_softkey(void);

// One Bulk-Only Transport command to the mounted UBOOT (core 1 only).
// buf must be word-aligned RAM; len == 0 means no data stage.
bool fm1_host_bot(const uint8_t *cdb, uint8_t cdb_len, bool in, void *buf, uint16_t len,
                  uint32_t *got);

// Queues a one-letter console command for core 1 (safe to call from core 0).
void fm1_pio_host_command(char c);
