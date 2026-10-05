# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

# Defines the `hammer_client` target for a suite. The client is built from
# the single shared source under common/servers/, so every suite that needs
# one compiles the same program — the runner picks the same executable for
# every suite and the only thing that changes is the flags on the command
# line.
#
# This mirrors common/open62541_add_server_executable.cmake, which does the
# same for the server; the two together keep "one shared source per
# benchmark role" consistent across the C half of the rig. A suite
# CMakeLists already includes that helper, so this one piggy-backs on it
# for open62541::open62541 being defined.
#
# Usage:
#   include(${CMAKE_CURRENT_LIST_DIR}/../../common/open62541_add_hammer_client_executable.cmake)

add_executable(hammer_client "${CMAKE_CURRENT_LIST_DIR}/servers/hammer_client.c")
# hammer_client.c includes "hammer_client.h" — resolve it from common/.
# The repository root is on the include path so that common/contract.h
# (transitively included by hammer_client.h) also resolves.
target_include_directories(hammer_client PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/.."
    "${CMAKE_CURRENT_LIST_DIR}")
target_link_libraries(hammer_client PRIVATE open62541::open62541)

# open62541/config.h only exposes glibc extensions such as
# PTHREAD_MUTEX_RECURSIVE when _GNU_SOURCE is defined before any system
# header. The client includes the standard C headers (via
# hammer_client.h) ahead of the open62541 headers, so define the feature-
# test macro on the command line to guarantee it is set first. Harmless
# on non-glibc platforms.
if(UNIX AND NOT APPLE)
    target_compile_definitions(hammer_client PRIVATE _GNU_SOURCE)
endif()
