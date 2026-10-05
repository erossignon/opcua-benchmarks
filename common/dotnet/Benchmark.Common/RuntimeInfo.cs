using System.Runtime;
using System.Runtime.InteropServices;
using System.Text.Json;
using Opc.Ua;

namespace Benchmark.Common;

public static class RuntimeInfo
{
    public static void Print(WorkerOptions options)
    {
        var quotas = Security.Quotas(options);
        var server = Security.ServerConfiguration(options);
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            runtime = Environment.Version.ToString(),
            architecture = RuntimeInformation.ProcessArchitecture.ToString(),
            gc_server = GCSettings.IsServerGC,
            gc_latency = GCSettings.LatencyMode.ToString(),
            jit = "runtime defaults; tiered compilation",
            clock = "Stopwatch.GetTimestamp/CLOCK_MONOTONIC; Int128 nanosecond conversion",
            profile = options.Profile,
            limits_source = "library constructors: ApplicationConfiguration.cs; throughput transport and request-queue overrides",
            limits = new { server.MaxSessionCount, server.MaxChannelCount, server.MinRequestThreadCount,
                server.MaxRequestThreadCount, server.MaxQueuedRequestCount, server.MaxRequestAge,
                server.MaxMessageQueueSize, server.MaxPublishRequestCount, server.OperationLimits,
                TransportQuotas = quotas }
        }));
    }
}
