#include "flash_info.h"

#include <string.h>

#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"

#include "flash_sfdp.h"

static fm1t_flash_info_t info;

// flash_do_cmd leaves XIP and restores it through the boot2 copy in RAM; with
// interrupts off and core 1 not yet running nothing executes from flash meanwhile.
static void sfdp_read(uint32_t addr, uint8_t *out, uint32_t n) {
    uint8_t tx[5 + 16] = {0x5A, (uint8_t)(addr >> 16), (uint8_t)(addr >> 8), (uint8_t)addr, 0};
    uint8_t rx[5 + 16];
    if (n > 16) n = 16;
    memset(&tx[5], 0, n);
    flash_do_cmd(tx, rx, 5 + n);
    memcpy(out, &rx[5], n);
}

void fm1t_flash_probe(void) {
    memset(&info, 0, sizeof(info));
    uint32_t irq = save_and_disable_interrupts();

    uint8_t tx[4] = {0x9F, 0, 0, 0}, rx[4];
    flash_do_cmd(tx, rx, 4);
    memcpy(info.jedec, &rx[1], 3);
    bool all_same = (rx[1] == rx[2] && rx[2] == rx[3]);
    info.jedec_ok = !(all_same && (rx[1] == 0x00 || rx[1] == 0xFF));

    uint8_t hdr[16];
    sfdp_read(0, hdr, 16);
    uint32_t bfpt, dwords;
    if (fm1t_sfdp_parse_header(hdr, &bfpt, &dwords, &info.sfdp_major, &info.sfdp_minor)) {
        uint8_t d[8];
        sfdp_read(bfpt, d, 8);
        uint32_t dw2 = (uint32_t)d[4] | (uint32_t)d[5] << 8 | (uint32_t)d[6] << 16 |
                       (uint32_t)d[7] << 24;
        info.sfdp_bytes = fm1t_sfdp_density_bytes(dw2);
        info.sfdp_ok = info.sfdp_bytes != 0;
    }

    restore_interrupts(irq);
    if (info.jedec_ok) info.jedec_bytes = fm1t_jedec_capacity_bytes(info.jedec);
}

const fm1t_flash_info_t *fm1t_flash_info(void) {
    return &info;
}
