#pragma once

// Pure parsing of SPI NOR identification data (no SDK dependencies, so the
// host tests compile it as is: tests/firmware_host).

#include <stdbool.h>
#include <stdint.h>

// SFDP header (first 16 bytes: 8-byte SFDP header + parameter header 0).
// On success returns true and the Basic Flash Parameter Table pointer/length.
bool fm1t_sfdp_parse_header(const uint8_t hdr[16], uint32_t *bfpt_addr, uint32_t *bfpt_dwords,
                            uint8_t *rev_major, uint8_t *rev_minor);

// BFPT DWORD 2 (flash memory density) to bytes; 0 if the value is unusable.
uint64_t fm1t_sfdp_density_bytes(uint32_t dword2);

// JEDEC 9Fh capacity byte as 2^n bytes, the convention of most 25-series parts
// (Winbond, GigaDevice, Puya, XMC ...). 0 if the byte is outside 2^16..2^28.
// Informative only; SFDP is the authoritative source.
uint32_t fm1t_jedec_capacity_bytes(const uint8_t id[3]);

// Manufacturer name for a few common JEDEC IDs, "unknown" otherwise.
const char *fm1t_jedec_vendor(uint8_t mfr);
