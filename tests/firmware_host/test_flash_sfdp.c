// Unit test of src/flash_sfdp.c (run by tests/test_firmware_host.py).
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "flash_sfdp.h"

int main(void) {
    // W25Q128JV-style SFDP header: rev 1.5, BFPT 16 DWORDs at 0x80
    const uint8_t hdr[16] = {'S', 'F', 'D', 'P', 0x05, 0x01, 0x00, 0xFF,
                             0x00, 0x05, 0x01, 0x10, 0x80, 0x00, 0x00, 0xFF};
    uint32_t addr = 0, dw = 0;
    uint8_t maj = 0, min = 0;
    assert(fm1t_sfdp_parse_header(hdr, &addr, &dw, &maj, &min));
    assert(addr == 0x80 && dw == 16 && maj == 1 && min == 5);

    uint8_t bad[16];
    memcpy(bad, hdr, 16);
    bad[0] = 'X';
    assert(!fm1t_sfdp_parse_header(bad, NULL, NULL, NULL, NULL));
    memset(bad, 0xFF, 16);               // no SFDP: bus reads all ones
    assert(!fm1t_sfdp_parse_header(bad, NULL, NULL, NULL, NULL));

    assert(fm1t_sfdp_density_bytes(0x07FFFFFF) == 16u * 1024 * 1024);  // 128 Mbit
    assert(fm1t_sfdp_density_bytes(0x00FFFFFF) == 2u * 1024 * 1024);   // 16 Mbit
    assert(fm1t_sfdp_density_bytes(0x80000021) == 1ull << 30);         // 2^33 bits
    assert(fm1t_sfdp_density_bytes(0xFFFFFFFF) == 0);
    assert(fm1t_sfdp_density_bytes(0) == 0);

    const uint8_t w25q128[3] = {0xEF, 0x40, 0x18}, w25q16[3] = {0xEF, 0x40, 0x15};
    const uint8_t junk[3] = {0xFF, 0xFF, 0xFF};
    assert(fm1t_jedec_capacity_bytes(w25q128) == 16u * 1024 * 1024);
    assert(fm1t_jedec_capacity_bytes(w25q16) == 2u * 1024 * 1024);
    assert(fm1t_jedec_capacity_bytes(junk) == 0);
    assert(!strcmp(fm1t_jedec_vendor(0xEF), "winbond"));
    assert(!strcmp(fm1t_jedec_vendor(0x42), "unknown"));
    puts("flash_sfdp OK");
    return 0;
}
