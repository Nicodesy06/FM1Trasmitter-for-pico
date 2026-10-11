// Host stub of the TinyUSB CDC device calls used by transporter_proto.c.
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))

uint32_t tud_cdc_n_write_str(uint8_t itf, const char *s);
uint32_t tud_cdc_n_write(uint8_t itf, const void *buf, uint32_t n);
uint32_t tud_cdc_n_write_flush(uint8_t itf);
bool tud_cdc_n_connected(uint8_t itf);
uint32_t tud_cdc_n_write_available(uint8_t itf);
