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
