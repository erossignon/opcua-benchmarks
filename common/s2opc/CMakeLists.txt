# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

cmake_minimum_required(VERSION 3.21)
project(o6_s2opc_server C)

# The S2OPC toolkit, mbedtls and expat installed under deps/s2opc by
# python -m bench.build --s2opc.
find_package(s2opc REQUIRED CONFIG)

add_executable(server server.c)
target_include_directories(server PRIVATE "${O6_REPOSITORY_ROOT}")
# The expat NodeSet loader is compiled into s2opc_clientserver when expat is found.
target_link_libraries(server PRIVATE s2opc_clientserver)
