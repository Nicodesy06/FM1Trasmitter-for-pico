#pragma once
#include <stdbool.h>
#include <stdint.h>

// Mock JieLi WL82 mask-ROM UBOOT1.00 + LoaderV2 behind the Bulk-Only
// Transport call the firmware makes (fm1_host_bot). The SPI flash is a 1 MiB
// NOR array in memory. Configuration from the environment:
//   MOCK_FLASH=<file>        initial flash image (1 MiB); default: a fixed pattern
//   MOCK_UBOOT=0             no UBOOT mounted (status uboot=0, info ERR no-uboot)
//   MOCK_CHIP_KEY=<hex>      chip key the loader reports (default 980F)
//   MOCK_FLASH_ID=<hex>      flash id (default 856014)
//   MOCK_FLAKY_READ=<n>      the n-th READ_FLASH (1-based) returns one flipped bit
//   MOCK_FAIL_READ=<n>       the n-th READ_FLASH fails at the BOT level (3 tries each)
//   MOCK_LOG=<file>          append one line per UBOOT/loader command
//   MOCK_FLASH_OUT=<file>    write the final flash image here on exit

void mock_init(void);
void mock_finish(void);
bool mock_uboot_ready(void);
bool mock_bot(const uint8_t *cdb, uint8_t cdb_len, bool in, void *buf, uint16_t len,
              uint32_t *got);
