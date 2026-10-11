#pragma once

#include <stdbool.h>
#include <stdint.h>

// Identification of the RP2040 board's own QSPI flash (NOT the FM-1's flash).
// Read once at boot, before core 1 starts and before USB, because the XIP
// builds execute from that flash.

typedef struct {
    bool jedec_ok;
    uint8_t jedec[3];
    bool sfdp_ok;
    uint8_t sfdp_major, sfdp_minor;
    uint64_t sfdp_bytes;        // 0 if SFDP is absent or unusable
    uint32_t jedec_bytes;       // 2^capacity-byte heuristic, 0 if unknown
} fm1t_flash_info_t;

void fm1t_flash_probe(void);
const fm1t_flash_info_t *fm1t_flash_info(void);
