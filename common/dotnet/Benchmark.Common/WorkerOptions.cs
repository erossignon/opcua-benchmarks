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

namespace Benchmark.Common;

public sealed class WorkerOptions
{
    private readonly Dictionary<string, string> values = new();
    public bool RuntimeInfo { get; }
    public WorkerOptions(string[] args)
    {
        var allowed = new HashSet<string> { "profile", "port", "security", "certificate", "private-key", "trust-certificate",
            "array-sizes", "endpoint", "iterations", "warmup", "samples", "worker", "operation", "max-outstanding", "seed", "batch-size", "array-size", "max-queued-requests" };
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--runtime-info") { RuntimeInfo = true; continue; }
            if (!args[i].StartsWith("--") || !allowed.Contains(args[i][2..]) || i + 1 == args.Length)
                throw new ArgumentException($"Unknown or incomplete option: {args[i]}");
            values.Add(args[i][2..], args[++i]);
        }
        if (Profile is not ("throughput" or "server_limits")) throw new ArgumentException("Invalid --profile");
        if (Security is not ("None" or "Basic256Sha256")) throw new ArgumentException("Invalid --security");
        if (Mode is not ("sync" or "async") || Operation is not ("read" or "write")) throw new ArgumentException("Invalid workload");
        if (Samples != 1) throw new ArgumentException("Workers require --samples 1");
        if (BatchSize > 1 && ArraySize > 1) throw new ArgumentException("Batched arrays are unsupported");
        if (ArraySizes.Length > 16 || ArraySizes.Any(x => x < 1) || ArraySizes.Distinct().Count() != ArraySizes.Length)
            throw new ArgumentException("Invalid ordered --array-sizes");
        if (ArraySize > 1) Contract.ArrayNodeId(ArraySizes, ArraySize);
        if (Security != "None" && new[] { "certificate", "private-key", "trust-certificate" }.Any(k => !values.ContainsKey(k)))
            throw new ArgumentException("Encrypted workers require certificate, private key, and trust certificate");
    }
    public string Get(string key, string fallback = "") => values.GetValueOrDefault(key, fallback);
    private int Positive(string key, int fallback)
    {
        int result = int.Parse(Get(key, fallback.ToString()));
        return result > 0 ? result : throw new ArgumentException($"--{key} must be positive");
    }
    public string Profile => Get("profile", "throughput");
    public int Port => Positive("port", 4840);
    public string Endpoint => Get("endpoint", Contract.Endpoint);
    public string Security => Get("security", "None");
    public string Mode => Get("worker", "sync");
    public string Operation => Get("operation", "read");
    public int Iterations => Positive("iterations", 1000);
    public int Warmup => Positive("warmup", 100);
    public int Samples => Positive("samples", 1);
    public int Depth => Mode == "sync" ? 1 : Positive("max-outstanding", 1);
    public int MaxQueuedRequests => Positive("max-queued-requests", 200);
    public uint Seed => uint.Parse(Get("seed", "1"));
    public int BatchSize => Positive("batch-size", 1);
    public int ArraySize => Positive("array-size", 1);
    public int[] ArraySizes => Get("array-sizes").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(int.Parse).ToArray();
}
