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

cmake_minimum_required(VERSION 3.21)
project(server_capacity C)

set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type")
set(UA_ENABLE_ENCRYPTION OFF CACHE STRING "" FORCE)
set(UA_MULTITHREADING 0 CACHE STRING "" FORCE)
set(UA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(UA_BUILD_UNIT_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory("../../deps/open62541" "open62541" EXCLUDE_FROM_ALL)
include("../../common/open62541_add_server_executable.cmake")
add_executable(client client.c)
target_include_directories(client PRIVATE ../../common)
target_link_libraries(client PRIVATE open62541::open62541)
if(UNIX AND NOT APPLE)
    target_compile_definitions(client PRIVATE _GNU_SOURCE)
endif()
