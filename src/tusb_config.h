#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

// rhport 0: RP2040 native USB, device, Mac-facing CDC console.
// rhport 1: Pico-PIO-USB on GP0/GP1, host, FM-1 JieLi ROM/UBOOT.

#define CFG_TUSB_OS                 OPT_OS_PICO
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN          __attribute__((aligned(4)))

// CFG_TUSB_DEBUG comes from CMake (LOG=2). Host enumeration logs at level 2
// go to our console; the device stack stays quiet.
#define CFG_TUH_LOG_LEVEL           2
#define CFG_TUD_LOG_LEVEL           3

//--------------------------------------------------------------------+
// Device (native USB)
//--------------------------------------------------------------------+
#define CFG_TUD_ENABLED             1
#define CFG_TUD_ENDPOINT0_SIZE      64
#define CFG_TUD_CDC                 2   // console + fm1t data
#define CFG_TUD_CDC_RX_BUFSIZE      256
#define CFG_TUD_CDC_TX_BUFSIZE      2048
#define CFG_TUD_CDC_EP_BUFSIZE      64

//--------------------------------------------------------------------+
// Host (PIO USB)
//--------------------------------------------------------------------+
#define CFG_TUH_ENABLED             1
#define CFG_TUH_RPI_PIO_USB         1
#define CFG_TUH_ENUMERATION_BUFSIZE 512   // stock V15 (4C4A:C755) has a 293-byte config
#define CFG_TUH_HUB                 0
#define CFG_TUH_DEVICE_MAX          1
// Required for tuh_edpt_xfer() completion callbacks; without it TinyUSB
// drops the completion of our raw bulk transfers.
#define CFG_TUH_API_EDPT_XFER       1
// No class drivers: the JieLi UBOOT speaks Bulk-Only Transport with vendor
// CDBs, so we drive its bulk endpoints directly instead of letting the MSC
// driver send its own SCSI commands at mount time.
#define CFG_TUH_MSC                 0

#endif
