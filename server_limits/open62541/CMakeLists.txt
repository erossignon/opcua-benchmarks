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
