# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

cmake_minimum_required(VERSION 3.16)
project(o6_python_open62541_benchmarks C)

set(O6_OPEN62541_SOURCE_DIR
    "${CMAKE_CURRENT_LIST_DIR}/../../deps/open62541"
    CACHE PATH "Path to the vendored open62541 source tree")
set(UA_ENABLE_ENCRYPTION OPENSSL CACHE STRING "Encryption backend" FORCE)
set(UA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(UA_BUILD_UNIT_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory("${O6_OPEN62541_SOURCE_DIR}"
                 "${CMAKE_CURRENT_BINARY_DIR}/open62541" EXCLUDE_FROM_ALL)

add_executable(client client.c)
target_link_libraries(client PRIVATE open62541::open62541)

include("${CMAKE_CURRENT_LIST_DIR}/../../common/open62541_add_server_executable.cmake")

# open62541/config.h only exposes glibc extensions such as
# PTHREAD_MUTEX_RECURSIVE when _GNU_SOURCE is defined before any system header.
# The client includes the standard C headers (via common.h) ahead of the
# open62541 headers, so define the feature-test macro on the command line to
# guarantee it is set first. Harmless on non-glibc platforms.
if(UNIX AND NOT APPLE)
    target_compile_definitions(client PRIVATE _GNU_SOURCE)
endif()
