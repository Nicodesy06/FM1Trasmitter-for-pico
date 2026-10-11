// Host stub of the few pico/stdlib.h facilities transporter_proto.c and
// jieli_uboot.c use (tests/firmware_host only).
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

uint64_t time_us_64(void);
void sleep_ms(uint32_t ms);
void tight_loop_contents(void);
