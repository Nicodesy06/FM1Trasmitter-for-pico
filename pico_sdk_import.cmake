# This file can be copied from the Raspberry Pi Pico SDK:
# external/pico_sdk_import.cmake
#
# Or configure with:
#   cmake -S . -B build -DPICO_SDK_PATH=/path/to/pico-sdk
#
# Keeping this placeholder avoids silently vendoring a stale SDK import helper.
if (DEFINED ENV{PICO_SDK_PATH} AND NOT PICO_SDK_PATH)
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
endif()

if (NOT PICO_SDK_PATH)
    message(FATAL_ERROR "Set PICO_SDK_PATH to your pico-sdk checkout")
endif()

include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)
