#include "board.h"

#include "pico/stdlib.h"
#include "hardware/gpio.h"

#define LED_TEST_STEP_MS 400
#define LED_TEST_STEPS 10

static volatile uint8_t want;           // last rgb() request, bits r=4 g=2 b=1
static volatile bool test_active;
static uint32_t test_step;
static uint64_t test_next_us;

#if FM1T_LED_KIND != FM1T_LED_NONE
static void led_init(uint pin, bool active_low) {
    gpio_init(pin);
    gpio_put(pin, active_low);          // off
    gpio_set_dir(pin, GPIO_OUT);
}
#endif

static void apply(uint8_t c) {
#if FM1T_LED_KIND == FM1T_LED_RGB
    gpio_put(FM1T_LED_RED_PIN, !!(c & 4) ^ FM1T_LED_RED_ACTIVE_LOW);
    gpio_put(FM1T_LED_GREEN_PIN, !!(c & 2) ^ FM1T_LED_GREEN_ACTIVE_LOW);
    gpio_put(FM1T_LED_BLUE_PIN, !!(c & 1) ^ FM1T_LED_BLUE_ACTIVE_LOW);
#elif FM1T_LED_KIND == FM1T_LED_MONO
    gpio_put(FM1T_LED_MONO_PIN, (c != 0) ^ FM1T_LED_MONO_ACTIVE_LOW);
#else
    (void)c;
#endif
}

void board_leds_init(void) {
#if FM1T_LED_KIND == FM1T_LED_RGB
    led_init(FM1T_LED_RED_PIN, FM1T_LED_RED_ACTIVE_LOW);
    led_init(FM1T_LED_GREEN_PIN, FM1T_LED_GREEN_ACTIVE_LOW);
    led_init(FM1T_LED_BLUE_PIN, FM1T_LED_BLUE_ACTIVE_LOW);
#elif FM1T_LED_KIND == FM1T_LED_MONO
    led_init(FM1T_LED_MONO_PIN, FM1T_LED_MONO_ACTIVE_LOW);
#endif
    want = 0;
}

void rgb(bool r, bool g, bool b) {
    uint8_t c = (uint8_t)((r ? 4 : 0) | (g ? 2 : 0) | (b ? 1 : 0));
    want = c;
    if (!test_active) apply(c);
}

void board_led_test_start(void) {
    test_step = 0;
    test_next_us = time_us_64();
    test_active = true;
}

// red, green, blue, white, off, ... (a single LED: on, off, on, off, ...)
void board_led_test_poll(void) {
    if (!test_active) return;
    uint64_t now = time_us_64();
    if (now < test_next_us) return;
    if (test_step >= LED_TEST_STEPS) {
        test_active = false;
        apply(want);
        return;
    }
#if FM1T_LED_KIND == FM1T_LED_RGB
    static const uint8_t seq[5] = {4, 2, 1, 7, 0};
    apply(seq[test_step % 5]);
#else
    apply((test_step & 1) ? 0 : 7);
#endif
    test_step++;
    test_next_us = now + LED_TEST_STEP_MS * 1000ull;
}

bool board_led_test_active(void) {
    return test_active;
}

const char *board_led_kind_name(void) {
#if FM1T_LED_KIND == FM1T_LED_RGB
    return "rgb";
#elif FM1T_LED_KIND == FM1T_LED_MONO
    return "mono";
#else
    return "none";
#endif
}
