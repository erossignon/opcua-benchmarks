# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

cmake_minimum_required(VERSION 3.16)
project(o6_server_limits_benchmarks C)

set(O6_OPEN62541_SOURCE_DIR
    "${CMAKE_CURRENT_LIST_DIR}/../../deps/open62541"
    CACHE PATH "Path to the vendored open62541 source tree")
set(UA_ENABLE_ENCRYPTION OFF CACHE STRING "Encryption backend" FORCE)
set(UA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(UA_BUILD_UNIT_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory("${O6_OPEN62541_SOURCE_DIR}"
                 "${CMAKE_CURRENT_BINARY_DIR}/open62541" EXCLUDE_FROM_ALL)

include("${CMAKE_CURRENT_LIST_DIR}/../../common/open62541_add_server_executable.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/../../common/open62541_add_hammer_client_executable.cmake")
