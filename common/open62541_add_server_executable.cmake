# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

# Defines the `server` target for a suite. The server is built from the single
# shared source under common/servers/, so every suite compiles the same
# open62541 server program — the runner picks the same executable for every
# suite and the only thing that changes is --array-sizes, --security and --port.
#
# Include this after open62541 has been added, so open62541::open62541 exists:
#   include(${CMAKE_CURRENT_LIST_DIR}/../../common/open62541_add_server_executable.cmake)

add_executable(server "${CMAKE_CURRENT_LIST_DIR}/servers/open62541_server.c")
target_include_directories(server PRIVATE "${CMAKE_CURRENT_LIST_DIR}/..")
target_link_libraries(server PRIVATE open62541::open62541)

# open62541/config.h only exposes glibc extensions such as
# PTHREAD_MUTEX_RECURSIVE when _GNU_SOURCE is defined before any system header.
# The server includes the standard C headers (via common.h) ahead of the
# open62541 headers, so define the feature-test macro on the command line to
# guarantee it is set first. Harmless on non-glibc platforms.
if(UNIX AND NOT APPLE)
    target_compile_definitions(server PRIVATE _GNU_SOURCE)
endif()
