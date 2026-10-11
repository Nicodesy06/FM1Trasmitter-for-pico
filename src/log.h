#pragma once

#include <stdint.h>

// stdout goes into a lock-free ring buffer so the timing-sensitive recovery
// and host code on core 1 never waits on USB. Core 0 drains it to the CDC
// port with log_drain().

void log_init(void);
void log_drain(void);
uint32_t log_dropped(void);

// Time-stamped log line, relative to log_init().
void dlog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
