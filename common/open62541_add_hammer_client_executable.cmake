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
