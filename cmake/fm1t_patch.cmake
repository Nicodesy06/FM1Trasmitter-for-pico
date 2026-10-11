# Copies Pico-PIO-USB's src/ into the build tree and applies
# lib/pico-pio-usb-bulk-multi-xact.patch to the copy (upstream did this with
# `patch -p1`). Portable to Windows: line endings of the copy and of the patch
# are normalised to LF first (a CRLF checkout breaks hunks), and the patch is
# applied with `patch` if there is one (Git for Windows ships it in usr/bin),
# else with `git apply` running outside any repository.

option(FM1T_FORCE_GIT_APPLY "Apply the Pico-PIO-USB patch with git apply even if patch exists" OFF)

function(fm1t_copy_and_patch SRC_DIR DEST PATCH_FILE)
    if (NOT EXISTS ${SRC_DIR}/pio_usb_host.c)
        message(FATAL_ERROR "FM-1 Transporter: ${SRC_DIR} is empty. Run `git submodule update --init` "
                            "(lib/Pico-PIO-USB).")
    endif()
    file(REMOVE_RECURSE ${DEST})
    file(GLOB _files RELATIVE ${SRC_DIR} ${SRC_DIR}/*)
    foreach(f ${_files})
        file(READ ${SRC_DIR}/${f} _c)
        string(REPLACE "\r\n" "\n" _c "${_c}")
        file(WRITE ${DEST}/src/${f} "${_c}")
    endforeach()
    file(READ ${PATCH_FILE} _p)
    string(REPLACE "\r\n" "\n" _p "${_p}")
    set(_lf_patch ${CMAKE_BINARY_DIR}/fm1t-pio-usb.patch)
    file(WRITE ${_lf_patch} "${_p}")

    find_program(FM1T_PATCH_EXE patch
        HINTS "$ENV{ProgramFiles}/Git/usr/bin" "$ENV{ProgramW6432}/Git/usr/bin" "C:/Program Files/Git/usr/bin")
    find_program(FM1T_GIT_EXE git)
    if (FM1T_PATCH_EXE AND NOT FM1T_FORCE_GIT_APPLY)
        execute_process(COMMAND ${FM1T_PATCH_EXE} -p1 --forward --quiet --binary -i ${_lf_patch}
            WORKING_DIRECTORY ${DEST} RESULT_VARIABLE _rc OUTPUT_VARIABLE _out ERROR_VARIABLE _out)
        set(_how "patch")
    elseif (FM1T_GIT_EXE)
        # GIT_CEILING_DIRECTORIES stops repository discovery at DEST's parent,
        # so git apply works on plain files even inside this repository's tree.
        get_filename_component(_parent ${DEST} DIRECTORY)
        execute_process(COMMAND ${CMAKE_COMMAND} -E env GIT_CEILING_DIRECTORIES=${_parent}
                ${FM1T_GIT_EXE} apply -p1 ${_lf_patch}
            WORKING_DIRECTORY ${DEST} RESULT_VARIABLE _rc OUTPUT_VARIABLE _out ERROR_VARIABLE _out)
        set(_how "git apply")
    else()
        message(FATAL_ERROR "FM-1 Transporter: neither `patch` nor `git` found to apply ${PATCH_FILE}")
    endif()
    if (NOT _rc EQUAL 0)
        message(FATAL_ERROR "FM-1 Transporter: applying ${PATCH_FILE} with ${_how} failed: ${_out}")
    endif()
    file(STRINGS ${DEST}/src/pio_usb_host.c _marker REGEX "PIO_USB_BULK_FRAME_BUDGET_US 700")
    if (NOT _marker)
        message(FATAL_ERROR "FM-1 Transporter: ${PATCH_FILE} did not take effect")
    endif()
    message(STATUS "FM-1 Transporter: Pico-PIO-USB patched with ${_how}")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${PATCH_FILE})
endfunction()
