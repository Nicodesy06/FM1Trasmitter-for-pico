cmake_minimum_required(VERSION 3.13)

# Pico-PIO-USB is vendored as a submodule, pinned to the release TinyUSB in
# the SDK expects. The build uses a patched copy in the build directory
# (lib/pico-pio-usb-bulk-multi-xact.patch: several bulk packets per frame
# instead of one), so the submodule itself stays pristine. The path must be
# set before the SDK pulls in TinyUSB.
set(FM1T_PIO_USB_SRC ${CMAKE_CURRENT_LIST_DIR}/lib/Pico-PIO-USB)
set(FM1T_PIO_USB_PATCH ${CMAKE_CURRENT_LIST_DIR}/lib/pico-pio-usb-bulk-multi-xact.patch)
set(PICO_PIO_USB_PATH ${CMAKE_BINARY_DIR}/Pico-PIO-USB-patched)
file(REMOVE_RECURSE ${PICO_PIO_USB_PATH})
file(COPY ${FM1T_PIO_USB_SRC}/src DESTINATION ${PICO_PIO_USB_PATH})
execute_process(
    COMMAND patch -p1 --forward --quiet -i ${FM1T_PIO_USB_PATCH}
    WORKING_DIRECTORY ${PICO_PIO_USB_PATH}
    RESULT_VARIABLE FM1T_PATCH_RESULT)
if (NOT FM1T_PATCH_RESULT EQUAL 0)
    message(FATAL_ERROR "Applying ${FM1T_PIO_USB_PATCH} failed")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${FM1T_PIO_USB_PATCH})

# TinyUSB debug level (CFG_TUSB_DEBUG). 2 logs host enumeration during M0.
set(LOG 2)

include(pico_sdk_import.cmake)

project(fm1_transporter C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

# JieLi wl82loader.bin (24064 B, from jl-uboot-tool) is embedded at build time
# so the transporter can run the flash loader. It is a third-party blob and
# is never committed; without it the firmware builds, but `info`/`read` fail.
set(FM1T_LOADER_BIN "$ENV{HOME}/.fm1/FM-1-RE/3rd-party/jl-uboot-tool/data/loaderblobs/usb/wl82loader.bin"
    CACHE FILEPATH "Path to wl82loader.bin")
set(FM1T_GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/generated)
if (EXISTS ${FM1T_LOADER_BIN})
    file(READ ${FM1T_LOADER_BIN} FM1T_LOADER_HEX HEX)
    string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," FM1T_LOADER_HEX "${FM1T_LOADER_HEX}")
    file(WRITE ${FM1T_GEN_DIR}/wl82loader.h
        "// Generated from ${FM1T_LOADER_BIN}. Do not commit.\n"
        "static const uint8_t wl82loader[] = {${FM1T_LOADER_HEX}};\n")
    set(FM1T_HAVE_LOADER 1)
    message(STATUS "FM-1 Transporter: embedding ${FM1T_LOADER_BIN}")
else()
    set(FM1T_HAVE_LOADER 0)
    message(WARNING "FM-1 Transporter: ${FM1T_LOADER_BIN} not found; building without the flash loader")
endif()

function(fm1t_add_firmware TARGET)
    add_executable(${TARGET}
        src/main.c
        src/recovery.c
        src/pio_host.c
        src/usb_device.c
        src/log.c
        src/jieli_uboot.c
        src/transporter_proto.c
    )

    pico_generate_pio_header(${TARGET}
        ${CMAKE_CURRENT_LIST_DIR}/pio/usb_key.pio
    )

    # tusb_config.h, generated wl82loader.h
    target_include_directories(${TARGET} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src ${FM1T_GEN_DIR})
    target_compile_definitions(${TARGET} PRIVATE FM1T_HAVE_LOADER=${FM1T_HAVE_LOADER})

    target_link_libraries(${TARGET}
        pico_stdlib
        pico_multicore
        pico_unique_id
        pico_usb_reset_interface_headers
        hardware_pio
        hardware_gpio
        hardware_clocks
        hardware_dma
        tinyusb_device
        tinyusb_host
        tinyusb_pico_pio_usb
    )

    # The console is our own TinyUSB CDC device (see src/usb_device.c,
    # src/log.c); pico_stdio_usb cannot coexist with the TinyUSB host stack.
    pico_enable_stdio_usb(${TARGET} 0)
    pico_enable_stdio_uart(${TARGET} 0)

    pico_add_extra_outputs(${TARGET})
endfunction()

# M0 firmware: USB_KEY recovery -> PIO USB host.
fm1t_add_firmware(fm1_transporter)

# Host bring-up firmware: PIO USB host only, no USB_KEY.
fm1t_add_firmware(fm1_transporter_hostonly)
target_compile_definitions(fm1_transporter_hostonly PRIVATE FM1T_HOST_ONLY=1)
