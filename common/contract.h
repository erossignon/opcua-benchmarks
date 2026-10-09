// SPDX-License-Identifier: AGPL-3.0-or-later
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
//
//    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

#ifndef O6_BENCHMARK_CONTRACT_H
#define O6_BENCHMARK_CONTRACT_H

/* Wire contract shared by every suite. Mirrors common/contract.py; the two
 * halves are kept honest by common/tests/test_contract.py. */

#include <errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "histogram.h" /* re-exported so every C program gets O6_HISTOGRAM_* and o6_histogram_bucket_for automatically */

#if defined(_WIN32)
# include <windows.h>
#else
# ifndef _POSIX_C_SOURCE
#  define _POSIX_C_SOURCE 200809L /* clock_gettime, CLOCK_MONOTONIC */
# endif
# include <time.h>
#endif

#define O6_FIRST_NODE_ID 1001u
#define O6_NODE_COUNT 100u
#define O6_ARRAY_FIRST_NODE_ID 2001u
#define O6_MAX_ARRAY_SIZES 16u
#define O6_DEFAULT_ENDPOINT "opc.tcp://127.0.0.1:4840"

/* The unified server readiness marker. Each runner matches this as a
 * substring; each server (in common/servers/) prints it. */
#define O6_SERVER_READY "benchmark server ready"

#define O6_LCG_MULTIPLIER 1664525u
#define O6_LCG_INCREMENT 1013904223u

static inline uint32_t
o6_next_node_index(uint32_t *state) {
    *state = (*state * O6_LCG_MULTIPLIER + O6_LCG_INCREMENT) & 0xFFFFFFFFu;
    return *state % O6_NODE_COUNT;
}

static inline uint64_t
o6_now_ns(void) {
#if defined(_WIN32)
    LARGE_INTEGER frequency;
    LARGE_INTEGER counter;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&counter);
    return (uint64_t)((1000000000.0 * (double)counter.QuadPart) /
                      (double)frequency.QuadPart);
#else
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
           (uint64_t)value.tv_nsec;
#endif
}

/* Raise the open62541 client cap that bites pipelines deeper than 32.
 * Fixed once here instead of in four places on 2026-08-13.
 *
 * A macro rather than a static inline function, because every suite's
 * common.h includes this header above the open62541 ones: a function body
 * here is compiled where it is written, where UA_ClientConfig does not exist
 * yet, so no amount of #ifdef around the body can make it act on a type the
 * translation unit has not seen. A macro is substituted at the call site,
 * which has. The earlier inline guarded its body on UA_TYPES_H_ and so
 * expanded to nothing in all three clients — the 32-call cap stayed in place
 * and every pipeline deeper than it was refused with BadTooManyOperations. A
 * call site that has not seen the open62541 headers now fails to compile,
 * which is the failure this is meant to have. */
#define o6_configure_client(config, max_outstanding)                          \
    ((config)->maxAsyncServiceCalls = (UA_UInt32)(max_outstanding))

#endif  /* O6_BENCHMARK_CONTRACT_H */
