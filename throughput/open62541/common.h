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

#ifndef O6_BENCHMARK_COMMON_H
#define O6_BENCHMARK_COMMON_H

#include <errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../common/contract.h"

#define O6_BENCHMARK_MAX_SAMPLES 31u
#define O6_BENCHMARK_EVENT_WAIT_MS 1000u
/* Per-request response timeout in milliseconds. The 5 s default is too tight
 * when many clients hammer a single server; a latency spike would otherwise
 * return BadTimeout and abort the worker. Raising it does not affect the
 * throughput of successful requests. */
#define O6_BENCHMARK_REQUEST_TIMEOUT_MS 60000u

/* The line the worker prints once it is connected and waiting for the runner
 * to release it. The shutdown is the runner's, not the worker's: it prints
 * "O6_BENCHMARK_READY", the runner responds with a newline on stdin, and the
 * timed measurement starts after both have happened. */
#define O6_BENCHMARK_READY "O6_BENCHMARK_READY"

/* Give up, saying what failed and where. The alternative this replaced was a
 * bare abort(), which leaves the runner reporting "client 0 failed with -6"
 * and nothing else: not the service that failed, not the line that gave up on
 * it, and — since SIGKILL and SIGABRT are told apart by number alone — easily
 * mistaken for the OOM killer. A worker that cannot be diagnosed from its
 * recorded output costs more of a long matrix than it saves. */
#define O6_BENCHMARK_DIE(reason)                                              \
    do {                                                                      \
        fprintf(stderr, "benchmark client gave up: %s (%s:%d)\n", (reason),   \
                __FILE__, __LINE__);                                          \
        fflush(stderr);                                                       \
        exit(EXIT_FAILURE);                                                   \
    } while(0)

typedef struct O6_BenchmarkOptions {
    size_t iterations;
    size_t warmup;
    size_t samples;
    const char *endpoint;
    const char *worker_mode;
    const char *operation;
    size_t max_outstanding;
    size_t seed;
    size_t batch_size;  /* nodes addressed by one Read/Write service call */
    size_t array_size;  /* elements per node; 1 selects the scalar block */
    size_t array_sizes[O6_MAX_ARRAY_SIZES];
    size_t array_size_count;
} O6_BenchmarkOptions;

typedef uint64_t (*O6_BenchmarkOperation)(void *context, size_t iterations);

/* Values moved by one service call: nodes per call times elements per node. */
static size_t
o6_benchmark_values_per_call(const O6_BenchmarkOptions *options) {
    return options->batch_size * options->array_size;
}

/* Index of options->array_size within the server's array set, or SIZE_MAX. */
static size_t
o6_benchmark_array_index(const O6_BenchmarkOptions *options) {
    size_t index;
    for(index = 0; index < options->array_size_count; ++index) {
        if(options->array_sizes[index] == options->array_size)
            return index;
    }
    return SIZE_MAX;
}

static uint64_t
o6_benchmark_now_ns(void) {
    return o6_now_ns();
}

static int
o6_benchmark_parse_size(const char *text, size_t *result) {
    char *end = NULL;
    unsigned long long parsed;
    errno = 0;
    parsed = strtoull(text, &end, 10);
    if(errno != 0 || !end || *end != '\0' || parsed == 0)
        return 0;
    *result = (size_t)parsed;
    return 1;
}

/* Parse a comma-separated element-count list. Named sizes (vga, hd, full_hd,
 * 4k) are resolved by the Python runner before the list reaches here, so only
 * integers are accepted. Duplicates are dropped: position fixes the NodeId. */
static int
o6_benchmark_parse_size_list(const char *text, size_t *values, size_t capacity,
                             size_t *count) {
    const char *cursor = text;
    *count = 0;
    if(!text || *text == '\0')
        return 1;
    while(*cursor != '\0') {
        char *end = NULL;
        unsigned long long parsed;
        size_t existing;
        int duplicate = 0;
        errno = 0;
        parsed = strtoull(cursor, &end, 10);
        if(errno != 0 || end == cursor || parsed == 0)
            return 0;
        for(existing = 0; existing < *count; ++existing) {
            if(values[existing] == (size_t)parsed)
                duplicate = 1;
        }
        if(!duplicate) {
            if(*count >= capacity)
                return 0;
            values[(*count)++] = (size_t)parsed;
        }
        cursor = end;
        if(*cursor == ',')
            ++cursor;
        else if(*cursor != '\0')
            return 0;
    }
    return 1;
}

static int
o6_benchmark_parse_options(int argc, char **argv,
                           O6_BenchmarkOptions *options) {
    int index;
    for(index = 1; index < argc; ++index) {
        if(strcmp(argv[index], "--batch-size") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->batch_size))
                return 0;
            continue;
        }
        if(strcmp(argv[index], "--array-size") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->array_size))
                return 0;
            continue;
        }
        if(strcmp(argv[index], "--array-sizes") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size_list(
                   argv[++index], options->array_sizes,
                   O6_MAX_ARRAY_SIZES, &options->array_size_count))
                return 0;
            continue;
        }
        if(strcmp(argv[index], "--iterations") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->iterations))
                return 0;
        } else if(strcmp(argv[index], "--warmup") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->warmup))
                return 0;
        } else if(strcmp(argv[index], "--samples") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->samples) ||
               options->samples > O6_BENCHMARK_MAX_SAMPLES)
                return 0;
        } else if(strcmp(argv[index], "--endpoint") == 0 && index + 1 < argc) {
            options->endpoint = argv[++index];
        } else if(strcmp(argv[index], "--worker") == 0 && index + 1 < argc) {
            options->worker_mode = argv[++index];
        } else if(strcmp(argv[index], "--operation") == 0 && index + 1 < argc) {
            options->operation = argv[++index];
            if(strcmp(options->operation, "read") != 0 &&
               strcmp(options->operation, "write") != 0)
                return 0;
        } else if(strcmp(argv[index], "--max-outstanding") == 0 &&
                  index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index],
                                        &options->max_outstanding))
                return 0;
        } else if(strcmp(argv[index], "--seed") == 0 && index + 1 < argc) {
            if(!o6_benchmark_parse_size(argv[++index], &options->seed))
                return 0;
        } else {
            return 0;
        }
    }
    if(options->batch_size == 0 || options->array_size == 0)
        return 0;
    /* Batched array access is not part of the matrix; vary one at a time. */
    if(options->batch_size > 1 && options->array_size > 1)
        return 0;
    if(options->array_size > 1 &&
       o6_benchmark_array_index(options) == SIZE_MAX)
        return 0;
    return 1;
}

static void
o6_benchmark_usage(const char *program) {
    fprintf(stderr,
            "Usage: %s [--iterations N] [--warmup N] [--samples N] "
            "[--endpoint URL] [--worker sync|async] "
            "[--operation read|write] "
            "[--max-outstanding N] [--seed N] "
            "[--batch-size N] [--array-size N] [--array-sizes N,N,...]\n",
            program);
}

static int
o6_benchmark_compare_double(const void *left, const void *right) {
    const double a = *(const double*)left;
    const double b = *(const double*)right;
    return (a > b) - (a < b);
}

static void
o6_benchmark_measure(const char *name, const char *implementation,
                     const O6_BenchmarkOptions *options,
                     O6_BenchmarkOperation operation, void *context) {
    double ns_per_operation[O6_BENCHMARK_MAX_SAMPLES];
    uint64_t checksum = operation(context, options->warmup);
    size_t sample;

    for(sample = 0; sample < options->samples; ++sample) {
        const uint64_t start = o6_benchmark_now_ns();
        checksum ^= operation(context, options->iterations);
        {
            const uint64_t elapsed = o6_benchmark_now_ns() - start;
            ns_per_operation[sample] =
                (double)elapsed / (double)options->iterations;
        }
    }

    qsort(ns_per_operation, options->samples, sizeof(double),
          o6_benchmark_compare_double);
    {
        const double minimum = ns_per_operation[0];
        const double median = ns_per_operation[options->samples / 2];
        const double maximum = ns_per_operation[options->samples - 1];
        const double values = (double)o6_benchmark_values_per_call(options);
        printf("{\"benchmark\":\"%s\",\"implementation\":\"%s\","
               "\"iterations\":%zu,\"values_per_operation\":%zu,"
               "\"samples\":%zu,"
               "\"median_ns_per_op\":%.3f,\"min_ns_per_op\":%.3f,"
               "\"max_ns_per_op\":%.3f,\"median_ops_per_second\":%.3f,"
               "\"median_values_per_second\":%.3f,"
               "\"checksum\":\"%" PRIu64 "\"}\n",
               name, implementation, options->iterations,
               o6_benchmark_values_per_call(options), options->samples,
               median, minimum, maximum, 1000000000.0 / median,
               values * 1000000000.0 / median, checksum);
    }
}

static int
o6_benchmark_measure_worker(const char *name, const char *implementation,
                            const O6_BenchmarkOptions *options,
                            O6_BenchmarkOperation operation, void *context) {
    uint64_t checksum = operation(context, options->warmup);
    uint64_t start;
    uint64_t end;
    puts("O6_BENCHMARK_READY");
    fflush(stdout);
    if(getchar() == EOF)
        return 0;
    start = o6_benchmark_now_ns();
    checksum ^= operation(context, options->iterations);
    end = o6_benchmark_now_ns();
    printf("{\"benchmark\":\"%s\",\"implementation\":\"%s\","
           "\"operations\":%zu,\"values_per_operation\":%zu,"
           "\"start_ns\":%" PRIu64 ","
           "\"end_ns\":%" PRIu64 ",\"checksum\":\"%" PRIu64 "\"}\n",
           name, implementation, options->iterations,
           o6_benchmark_values_per_call(options), start, end, checksum);
    fflush(stdout);
    return 1;
}

#endif
