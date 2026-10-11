#include "flash_sfdp.h"

bool fm1t_sfdp_parse_header(const uint8_t hdr[16], uint32_t *bfpt_addr, uint32_t *bfpt_dwords,
                            uint8_t *rev_major, uint8_t *rev_minor) {
    // "SFDP" signature, little-endian 0x50444653
    if (hdr[0] != 'S' || hdr[1] != 'F' || hdr[2] != 'D' || hdr[3] != 'P') return false;
    if (hdr[5] != 1) return false;          // SFDP major revision 1 (JESD216 family)
    // Parameter header 0 must be the JEDEC Basic Flash Parameter Table:
    // ID LSB 0x00 (byte 8), MSB 0xFF (byte 15) for JESD216A+, or 0x00 only.
    if (hdr[8] != 0x00) return false;
    uint32_t len = hdr[11];
    uint32_t ptr = (uint32_t)hdr[12] | (uint32_t)hdr[13] << 8 | (uint32_t)hdr[14] << 16;
    if (len < 2 || ptr == 0 || ptr > 0xFFFF) return false;
    if (bfpt_addr) *bfpt_addr = ptr;
    if (bfpt_dwords) *bfpt_dwords = len;
    if (rev_major) *rev_major = hdr[5];
    if (rev_minor) *rev_minor = hdr[4];
    return true;
}

uint64_t fm1t_sfdp_density_bytes(uint32_t dword2) {
    if (dword2 == 0xFFFFFFFFu || dword2 == 0) return 0;
    uint64_t bits;
    if (dword2 & 0x80000000u) {
        uint32_t n = dword2 & 0x7FFFFFFFu;
        if (n < 3 || n > 40) return 0;
        bits = 1ull << n;
    } else {
        bits = (uint64_t)dword2 + 1;
    }
    if (bits % 8) return 0;
    return bits / 8;
}

uint32_t fm1t_jedec_capacity_bytes(const uint8_t id[3]) {
    uint8_t c = id[2];
    if (c < 16 || c > 28) return 0;
    return 1u << c;
}

const char *fm1t_jedec_vendor(uint8_t mfr) {
    switch (mfr) {
    case 0xEF: return "winbond";
    case 0xC8: return "gigadevice";
    case 0x85: return "puya";
    case 0x20: return "xmc_or_micron";
    case 0x68: return "boya";
    case 0x5E: return "zbit";
    case 0x9D: return "issi";
    case 0x1F: return "adesto";
    case 0xC2: return "macronix";
    case 0x0B: return "xtx";
    case 0xBA: return "zetta";
    default: return "unknown";
    }
}
