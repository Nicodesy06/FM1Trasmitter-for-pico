// Mac-facing USB device on the RP2040 native controller (rhport 0):
//   CDC 0: log console (src/log.c, one-letter diagnostics)
//   CDC 1: fm1t data channel (src/transporter_proto.c)
//   Raspberry Pi reset interface, so `picotool -f` and a 1200 baud touch on
//   the console still reboot the board into BOOTSEL.

#include "pico/bootrom.h"
#include "pico/unique_id.h"
#include "pico/usb_reset_interface.h"
#include "hardware/watchdog.h"
#include "tusb.h"
#include "device/usbd_pvt.h"

#define USBD_VID 0x2E8A     // Raspberry Pi
#define USBD_PID 0x000A     // Pico SDK CDC (RP2040), what picotool expects

#define TUD_RPI_RESET_DESC_LEN 9
#define USBD_DESC_LEN (TUD_CONFIG_DESC_LEN + 2 * TUD_CDC_DESC_LEN + TUD_RPI_RESET_DESC_LEN)

#define USBD_ITF_CDC0 0     // two interfaces each
#define USBD_ITF_CDC1 2
#define USBD_ITF_RESET 4
#define USBD_ITF_MAX 5

enum { STR_LANG, STR_MANUF, STR_PRODUCT, STR_SERIAL, STR_CDC0, STR_CDC1, STR_RESET, STR_COUNT };

static const tusb_desc_device_t desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USBD_VID,
    .idProduct = USBD_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = STR_MANUF,
    .iProduct = STR_PRODUCT,
    .iSerialNumber = STR_SERIAL,
    .bNumConfigurations = 1,
};

static const uint8_t desc_cfg[USBD_DESC_LEN] = {
    TUD_CONFIG_DESCRIPTOR(1, USBD_ITF_MAX, 0, USBD_DESC_LEN, 0, 100),
    TUD_CDC_DESCRIPTOR(USBD_ITF_CDC0, STR_CDC0, 0x81, 8, 0x02, 0x82, 64),
    TUD_CDC_DESCRIPTOR(USBD_ITF_CDC1, STR_CDC1, 0x83, 8, 0x04, 0x84, 64),
    9, TUSB_DESC_INTERFACE, USBD_ITF_RESET, 0, 0, TUSB_CLASS_VENDOR_SPECIFIC,
    RESET_INTERFACE_SUBCLASS, RESET_INTERFACE_PROTOCOL, STR_RESET,
};

static char serial_str[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];

static const char *const desc_str[STR_COUNT] = {
    [STR_MANUF] = "kurogedelic",
    [STR_PRODUCT] = "FM-1 Transporter",
    [STR_SERIAL] = serial_str,
    [STR_CDC0] = "FM-1 Transporter console",
    [STR_CDC1] = "FM-1 Transporter data",
    [STR_RESET] = "Reset",
};

const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&desc_device;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_cfg;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    static uint16_t out[32];
    uint8_t len;

    if (!serial_str[0]) pico_get_unique_board_id_string(serial_str, sizeof(serial_str));

    if (index == STR_LANG) {
        out[1] = 0x0409;
        len = 1;
    } else {
        if (index >= STR_COUNT) return NULL;
        const char *s = desc_str[index];
        for (len = 0; len < 31 && s[len]; len++) out[1 + len] = (uint8_t)s[len];
    }

    out[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
    return out;
}

//--------------------------------------------------------------------+
// Reset interface (same protocol as pico_stdio_usb's reset_interface.c,
// which is compiled out whenever the TinyUSB host stack is linked)
//--------------------------------------------------------------------+

static uint8_t reset_itf;

static void resetd_init(void) {}

static void resetd_reset(uint8_t rhport) {
    (void)rhport;
    reset_itf = 0;
}

static uint16_t resetd_open(uint8_t rhport, tusb_desc_interface_t const *itf, uint16_t max_len) {
    (void)rhport;
    TU_VERIFY(itf->bInterfaceClass == TUSB_CLASS_VENDOR_SPECIFIC &&
              itf->bInterfaceSubClass == RESET_INTERFACE_SUBCLASS &&
              itf->bInterfaceProtocol == RESET_INTERFACE_PROTOCOL, 0);
    TU_VERIFY(max_len >= sizeof(tusb_desc_interface_t), 0);
    reset_itf = itf->bInterfaceNumber;
    return sizeof(tusb_desc_interface_t);
}

static bool resetd_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *req) {
    (void)rhport;
    if (stage != CONTROL_STAGE_SETUP) return true;
    if (req->wIndex != reset_itf) return false;

    if (req->bRequest == RESET_REQUEST_BOOTSEL) {
        int gpio = (req->wValue & 0x100) ? (int)(req->wValue >> 9) : -1;
        rom_reset_usb_boot_extra(gpio, req->wValue & 0x7f, req->wValue & 0x200);
        // does not return
    }

    if (req->bRequest == RESET_REQUEST_FLASH) {
        watchdog_reboot(0, 0, 100);
        return true;
    }

    return false;
}

static bool resetd_xfer_cb(uint8_t rhport, uint8_t ep, xfer_result_t result, uint32_t n) {
    (void)rhport; (void)ep; (void)result; (void)n;
    return true;
}

static const usbd_class_driver_t resetd_driver = {
    .init = resetd_init,
    .reset = resetd_reset,
    .open = resetd_open,
    .control_xfer_cb = resetd_control_xfer_cb,
    .xfer_cb = resetd_xfer_cb,
    .sof = NULL,
};

usbd_class_driver_t const *usbd_app_driver_get_cb(uint8_t *driver_count) {
    *driver_count = 1;
    return &resetd_driver;
}

void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *coding) {
    if (itf == 0 && coding->bit_rate == 1200) rom_reset_usb_boot_extra(-1, 0, false);
}
