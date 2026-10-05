// SPDX-FileCopyrightText: 2026 o6 Automation GmbH
// All rights reserved.

#ifndef O6_BENCHMARK_HISTOGRAM_H
#define O6_BENCHMARK_HISTOGRAM_H

/* Logarithmic latency histogram shared by every suite. The Python half lives
 * in common/histogram.py; the two are kept honest by common/tests/test_histogram_contract.py.
 *
 * SUBBUCKETS linear steps sit inside each octave: bucket (octave * SUBBUCKETS
 * + sub) covers [2**octave * (1 + sub/SUBBUCKETS), 2**octave * (1 + (sub+1)/SUBBUCKETS)).
 *
 * One bucket per octave would be cheaper, but a percentile read off it is
 * only known to a factor of two — not precision, a different number — and it
 * makes a configured threshold unreachable: against a 0.512 ms baseline the
 * only p99 values that exist are 0.512, 1.024, 2.048, 4.096, 8.192, so a
 * "10x" rule cannot fire until 16x. Sixteen steps per octave puts every
 * bucket within about 7% of its neighbour, which a threshold and a reported
 * figure can both stand on.
 *
 * 30 octaves reach about 18 minutes, far past anything a benchmark would
 * still be calling "waiting". */

#include <stdint.h>

#define O6_HISTOGRAM_SUBBUCKETS 16u
#define O6_HISTOGRAM_OCTAVES 30u
#define O6_HISTOGRAM_BUCKETS \
    (O6_HISTOGRAM_SUBBUCKETS * O6_HISTOGRAM_OCTAVES)

/* The bucket a latency (in microseconds) falls into: its octave, then which
 * linear step inside that octave, capped at the last bucket. */
static inline unsigned
o6_histogram_bucket_for(uint64_t latency_us) {
    unsigned octave = 0;
    uint64_t base;
    unsigned sub;
    if(latency_us == 0)
        return 0;
    while(octave + 1 < O6_HISTOGRAM_OCTAVES &&
          latency_us >= (UINT64_C(1) << (octave + 1)))
        ++octave;
    base = UINT64_C(1) << octave;
    sub = (unsigned)(((latency_us - base) * O6_HISTOGRAM_SUBBUCKETS) / base);
    if(sub >= O6_HISTOGRAM_SUBBUCKETS)
        sub = O6_HISTOGRAM_SUBBUCKETS - 1;
    return octave * O6_HISTOGRAM_SUBBUCKETS + sub;
}

#endif  /* O6_BENCHMARK_HISTOGRAM_H */
