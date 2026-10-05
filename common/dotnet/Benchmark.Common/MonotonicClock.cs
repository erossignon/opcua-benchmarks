using System.Diagnostics;

namespace Benchmark.Common;

public static class MonotonicClock
{
    // On Unix the pinned runtime uses clock_gettime(CLOCK_MONOTONIC).
    // Int128 multiplication preserves nanoseconds without long overflow.
    public static long Now() => checked((long)((Int128)Stopwatch.GetTimestamp() * 1_000_000_000 / Stopwatch.Frequency));
}
