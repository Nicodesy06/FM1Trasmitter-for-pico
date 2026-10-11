# FM-1 Transporter board profiles.
#
# A profile is boards/<id>.json; it is the single source of truth for the
# firmware (this file turns it into PICO_BOARD and a generated
# fm1t_board_config.h) and for the host tools (host/fm1_board_tool).
#
#   -DFM1T_BOARD=<id>              select a profile (see boards/*.json)
#   -DFM1T_ALLOW_UNVALIDATED=ON    allow profiles with "enabled": false
#   -DFM1T_LEDS=OFF                never drive any LED GPIO
#
# Without FM1T_BOARD, a PICO_BOARD given on the command line picks the profile
# that uses it (upstream's documented `-DPICO_BOARD=seeed_xiao_rp2040` keeps
# working); with neither, the reference XIAO RP2040 profile is used.
#
# Must be included before pico_sdk_import.cmake (it sets PICO_BOARD).

cmake_minimum_required(VERSION 3.19)    # string(JSON)

set(FM1T_BOARDS_DIR ${CMAKE_CURRENT_LIST_DIR}/../boards)
get_filename_component(FM1T_BOARDS_DIR ${FM1T_BOARDS_DIR} ABSOLUTE)

option(FM1T_ALLOW_UNVALIDATED "Allow board profiles that are disabled until validated" OFF)
option(FM1T_LEDS "Drive the board's status LED(s)" ON)

file(GLOB FM1T_PROFILE_FILES ${FM1T_BOARDS_DIR}/*.json)
set(FM1T_ALL_BOARDS "")
foreach(f ${FM1T_PROFILE_FILES})
    get_filename_component(n ${f} NAME_WE)
    list(APPEND FM1T_ALL_BOARDS ${n})
endforeach()

if (NOT FM1T_BOARD)
    if (DEFINED PICO_BOARD AND NOT PICO_BOARD STREQUAL "")
        foreach(n ${FM1T_ALL_BOARDS})
            file(READ ${FM1T_BOARDS_DIR}/${n}.json _j)
            string(JSON _pb GET "${_j}" pico_board)
            if (_pb STREQUAL PICO_BOARD)
                set(FM1T_BOARD ${n})
            endif()
        endforeach()
        if (NOT FM1T_BOARD)
            message(FATAL_ERROR "FM-1 Transporter: no board profile uses PICO_BOARD=${PICO_BOARD}. "
                                "Pick one with -DFM1T_BOARD=<id>: ${FM1T_ALL_BOARDS}")
        endif()
    else()
        set(FM1T_BOARD xiao_rp2040)
    endif()
endif()
set(FM1T_BOARD ${FM1T_BOARD} CACHE STRING "FM-1 Transporter board profile (boards/<id>.json)" FORCE)
set_property(CACHE FM1T_BOARD PROPERTY STRINGS ${FM1T_ALL_BOARDS})

set(FM1T_PROFILE ${FM1T_BOARDS_DIR}/${FM1T_BOARD}.json)
if (NOT EXISTS ${FM1T_PROFILE})
    message(FATAL_ERROR "FM-1 Transporter: unknown board '${FM1T_BOARD}'. Known: ${FM1T_ALL_BOARDS}")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${FM1T_PROFILE})
file(READ ${FM1T_PROFILE} FM1T_JSON)

macro(fm1t_json OUT)
    string(JSON ${OUT} ERROR_VARIABLE _err GET "${FM1T_JSON}" ${ARGN})
    if (_err)
        message(FATAL_ERROR "FM-1 Transporter: ${FM1T_PROFILE}: missing ${ARGN} (${_err})")
    endif()
endmacro()
macro(fm1t_json_opt OUT DEFAULT)
    string(JSON ${OUT} ERROR_VARIABLE _err GET "${FM1T_JSON}" ${ARGN})
    if (_err OR ${OUT} STREQUAL "${ARGN}-NOTFOUND")
        set(${OUT} ${DEFAULT})
    endif()
endmacro()

fm1t_json(_id id)
if (NOT _id STREQUAL FM1T_BOARD)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_PROFILE} has id '${_id}', expected '${FM1T_BOARD}'")
endif()
fm1t_json(FM1T_BOARD_NAME name)
fm1t_json(FM1T_BOARD_ENABLED enabled)
fm1t_json(FM1T_SOC soc)
fm1t_json(FM1T_UF2_NAME uf2_name)
fm1t_json(FM1T_PICO_BOARD pico_board)
fm1t_json(FM1T_PICO_BOARD_HEADER pico_board_header)
fm1t_json(FM1T_BINARY_TYPE binary_type)
fm1t_json(FM1T_VALIDATION validation level)
fm1t_json(FM1T_PIN_DP usb_pio dp)
fm1t_json(FM1T_PIN_DM usb_pio dm)
fm1t_json(FM1T_PINOUT usb_pio pinout)
fm1t_json(FM1T_RECOVERY_PIO usb_pio recovery_pio)
fm1t_json(FM1T_HOST_PIO usb_pio host_pio)
fm1t_json(FM1T_LABEL_DP usb_pio labels dp)
fm1t_json(FM1T_LABEL_DM usb_pio labels dm)
fm1t_json(FM1T_SYS_KHZ clock sys_khz)
fm1t_json(FM1T_XOSC_HZ clock xosc_hz)
fm1t_json(FM1T_LED_KIND leds kind)
fm1t_json(FM1T_FLASH_DECLARED flash declared_bytes)
fm1t_json(FM1T_FLASH_BUILD flash build_bytes)

if (NOT FM1T_BOARD_ENABLED AND NOT FM1T_ALLOW_UNVALIDATED)
    message(FATAL_ERROR "FM-1 Transporter: board '${FM1T_BOARD}' is disabled until its pinout is "
                        "validated (${FM1T_VALIDATION}). Pass -DFM1T_ALLOW_UNVALIDATED=ON only if you "
                        "have checked the pinout yourself.")
endif()

# --- validation (mirrors host/fm1_board_tool/profiles.py) -------------------
if (NOT FM1T_SOC STREQUAL "RP2040")
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: only RP2040 is supported (RP2350 A2 has "
                        "the E9 pull-down erratum that breaks the D+ sensing)")
endif()
if (NOT FM1T_PINOUT STREQUAL "DPDM")
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: pinout must be DPDM (D- = D+ + 1): the "
                        "USB_KEY program sets both pins as one consecutive group")
endif()
math(EXPR _dm_expected "${FM1T_PIN_DP} + 1")
if (NOT FM1T_PIN_DM EQUAL _dm_expected)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: D- (GP${FM1T_PIN_DM}) must be D+ + 1 "
                        "(Pico-PIO-USB PIO_USB_PINOUT_DPDM and the usb_key program need it)")
endif()
if (FM1T_PIN_DP LESS 0 OR FM1T_PIN_DM GREATER 29)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: D+/D- must be GPIO 0..29")
endif()
if (FM1T_RECOVERY_PIO EQUAL FM1T_HOST_PIO OR FM1T_RECOVERY_PIO GREATER 1 OR FM1T_HOST_PIO GREATER 1)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: recovery and host need different PIO "
                        "blocks (0 and 1): Pico-PIO-USB uses all of one")
endif()
if (NOT FM1T_SYS_KHZ EQUAL 120000)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: clk_sys must stay 120000 kHz, the "
                        "frequency the USB_KEY timing and Pico-PIO-USB were validated at")
endif()
if (NOT FM1T_XOSC_HZ EQUAL 12000000)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: a 12 MHz crystal is required")
endif()

set(FM1T_RESERVED_MASK 0)
string(JSON _nres LENGTH "${FM1T_JSON}" reserved_gpios)
if (_nres GREATER 0)
    math(EXPR _last "${_nres} - 1")
    foreach(i RANGE ${_last})
        string(JSON _g MEMBER "${FM1T_JSON}" reserved_gpios ${i})
        if (_g EQUAL FM1T_PIN_DP OR _g EQUAL FM1T_PIN_DM)
            message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: GP${_g} is reserved on this board")
        endif()
        math(EXPR FM1T_RESERVED_MASK "${FM1T_RESERVED_MASK} | (1 << ${_g})" OUTPUT_FORMAT HEXADECIMAL)
    endforeach()
endif()

set(FM1T_LED_KIND_NUM 0)
set(FM1T_LED_DEFS "")
if (FM1T_LEDS AND NOT FM1T_LED_KIND STREQUAL "none")
    if (FM1T_LED_KIND STREQUAL "mono")
        set(FM1T_LED_KIND_NUM 1)
        set(_leds mono)
    elseif (FM1T_LED_KIND STREQUAL "rgb")
        set(FM1T_LED_KIND_NUM 2)
        set(_leds red green blue)
    else()
        message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: leds.kind must be none, mono or rgb")
    endif()
    foreach(c ${_leds})
        fm1t_json(_pin leds ${c} gpio)
        fm1t_json(_low leds ${c} active_low)
        if (_pin EQUAL FM1T_PIN_DP OR _pin EQUAL FM1T_PIN_DM)
            message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: LED ${c} on GP${_pin} collides with D+/D-")
        endif()
        if (_nres GREATER 0)
            string(JSON _r ERROR_VARIABLE _e GET "${FM1T_JSON}" reserved_gpios ${_pin})
            if (NOT _e)
                message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: LED ${c} on reserved GP${_pin}")
            endif()
        endif()
        string(TOUPPER ${c} C)
        if (_low)
            set(_low 1)
        else()
            set(_low 0)
        endif()
        string(APPEND FM1T_LED_DEFS "#define FM1T_LED_${C}_PIN ${_pin}\n#define FM1T_LED_${C}_ACTIVE_LOW ${_low}\n")
    endforeach()
endif()

if (FM1T_FLASH_BUILD GREATER FM1T_FLASH_DECLARED)
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: flash.build_bytes exceeds declared_bytes")
endif()

# --- SDK board selection ---------------------------------------------------
if (DEFINED PICO_BOARD AND NOT PICO_BOARD STREQUAL "" AND NOT PICO_BOARD STREQUAL FM1T_PICO_BOARD)
    message(FATAL_ERROR "FM-1 Transporter: PICO_BOARD=${PICO_BOARD} conflicts with profile "
                        "${FM1T_BOARD} (${FM1T_PICO_BOARD}). Pass only -DFM1T_BOARD.")
endif()
set(PICO_BOARD ${FM1T_PICO_BOARD} CACHE STRING "Pico SDK board (from the FM-1 board profile)" FORCE)
if (FM1T_PICO_BOARD_HEADER STREQUAL "boards/include")
    list(APPEND PICO_BOARD_HEADER_DIRS ${FM1T_BOARDS_DIR}/include/boards)
    set(PICO_BOARD_HEADER_DIRS ${PICO_BOARD_HEADER_DIRS} CACHE STRING "" FORCE)
elseif (NOT FM1T_PICO_BOARD_HEADER STREQUAL "sdk")
    message(FATAL_ERROR "FM-1 Transporter: ${FM1T_BOARD}: pico_board_header must be sdk or boards/include")
endif()

message(STATUS "FM-1 Transporter: board ${FM1T_BOARD} (${FM1T_BOARD_NAME}), PICO_BOARD=${PICO_BOARD}, "
               "D+=GP${FM1T_PIN_DP} D-=GP${FM1T_PIN_DM}, LEDs=${FM1T_LED_KIND_NUM}, "
               "binary=${FM1T_BINARY_TYPE}, validation=${FM1T_VALIDATION}")

# --- build identification --------------------------------------------------
set(FM1T_FW_VERSION "1.0.0-mb")
set(FM1T_UPSTREAM "a632d92")
set(FM1T_GIT_REV "nogit")
set(FM1T_SOURCE_DATE "unknown")
find_package(Git QUIET)
if (GIT_FOUND)
    execute_process(COMMAND ${GIT_EXECUTABLE} rev-parse --short=10 HEAD
        WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/.. OUTPUT_VARIABLE _rev
        OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET RESULT_VARIABLE _rc)
    if (_rc EQUAL 0 AND _rev)
        set(FM1T_GIT_REV ${_rev})
        execute_process(COMMAND ${GIT_EXECUTABLE} log -1 --format=%cs
            WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/.. OUTPUT_VARIABLE FM1T_SOURCE_DATE
            OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
        execute_process(COMMAND ${GIT_EXECUTABLE} status --porcelain --untracked-files=no
            -- src pio boards cmake CMakeLists.txt lib/pico-pio-usb-bulk-multi-xact.patch
            WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/.. OUTPUT_VARIABLE _dirty ERROR_QUIET)
        if (_dirty)
            set(FM1T_GIT_REV "${FM1T_GIT_REV}-dirty")
        endif()
    endif()
endif()
set(FM1T_BUILD_ID "${FM1T_GIT_REV}" CACHE STRING "Build identifier reported by the firmware")

function(fm1t_write_board_config DIR)
    set(_h "// Generated by cmake/fm1t_board.cmake from boards/${FM1T_BOARD}.json. Do not edit.\n")
    string(APPEND _h "#pragma once\n\n")
    string(APPEND _h "#define FM1T_BOARD_ID \"${FM1T_BOARD}\"\n")
    string(APPEND _h "#define FM1T_BOARD_NAME \"${FM1T_BOARD_NAME}\"\n")
    string(APPEND _h "#define FM1T_BOARD_VALIDATION \"${FM1T_VALIDATION}\"\n")
    string(APPEND _h "#define FM1T_FW_VERSION \"${FM1T_FW_VERSION}\"\n")
    string(APPEND _h "#define FM1T_UPSTREAM \"${FM1T_UPSTREAM}\"\n")
    string(APPEND _h "#define FM1T_BUILD_ID \"${FM1T_BUILD_ID}\"\n")
    string(APPEND _h "#define FM1T_BINARY_TYPE \"${FM1T_BINARY_TYPE}\"\n")
    string(APPEND _h "#define FM1T_PIN_DP ${FM1T_PIN_DP}\n#define FM1T_PIN_DM ${FM1T_PIN_DM}\n")
    string(APPEND _h "#define FM1T_LABEL_DP \"${FM1T_LABEL_DP}\"\n#define FM1T_LABEL_DM \"${FM1T_LABEL_DM}\"\n")
    string(APPEND _h "#define FM1T_RECOVERY_PIO ${FM1T_RECOVERY_PIO}\n#define FM1T_HOST_PIO ${FM1T_HOST_PIO}\n")
    string(APPEND _h "#define FM1T_SYS_KHZ ${FM1T_SYS_KHZ}\n")
    string(APPEND _h "#define FM1T_FLASH_DECLARED_BYTES ${FM1T_FLASH_DECLARED}u\n")
    string(APPEND _h "#define FM1T_FLASH_BUILD_BYTES ${FM1T_FLASH_BUILD}u\n")
    string(APPEND _h "#define FM1T_RESERVED_MASK ${FM1T_RESERVED_MASK}u\n")
    string(APPEND _h "#define FM1T_LED_KIND ${FM1T_LED_KIND_NUM}     // 0 none, 1 mono, 2 rgb\n")
    string(APPEND _h "${FM1T_LED_DEFS}")
    file(CONFIGURE OUTPUT ${DIR}/fm1t_board_config.h CONTENT "${_h}")
endfunction()

# Per-target settings, called after pico_sdk_init().
function(fm1t_apply_board TARGET)
    if (NOT FM1T_BINARY_TYPE STREQUAL "default")
        pico_set_binary_type(${TARGET} ${FM1T_BINARY_TYPE})
    endif()
    pico_set_program_name(${TARGET} "fm1_transporter")
    pico_set_program_version(${TARGET} "${FM1T_FW_VERSION}+${FM1T_BUILD_ID}")
    pico_set_program_url(${TARGET} "https://github.com/Nicodesy06/FM1Trasmitter-for-pico")
    # Commit date instead of __DATE__ in the binary info, so a commit builds
    # to the same bytes on any day (upstream's image embeds __DATE__).
    target_compile_definitions(${TARGET} PRIVATE PICO_PROGRAM_BUILD_DATE="${FM1T_SOURCE_DATE}")
endfunction()
