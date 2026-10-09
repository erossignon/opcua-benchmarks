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
