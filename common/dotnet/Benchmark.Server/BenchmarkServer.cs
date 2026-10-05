using Benchmark.Common;
using Opc.Ua;
using Opc.Ua.Server;

namespace Benchmark.Server;

public sealed class BenchmarkServer(WorkerOptions options) : StandardServer
{
    protected override MasterNodeManager CreateMasterNodeManager(IServerInternal server, ApplicationConfiguration configuration)
        => new(server, configuration, null, new BenchmarkNodeManager(server, configuration, options.ArraySizes));
}
