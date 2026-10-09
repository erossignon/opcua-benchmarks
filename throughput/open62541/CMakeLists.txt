# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
#    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

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
