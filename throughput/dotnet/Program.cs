using System.Text.Json;
using Benchmark.Common;
using Benchmark.Client;
using Opc.Ua;
using Opc.Ua.Client;

try
{
    var options = new WorkerOptions(args);
    if (options.RuntimeInfo) { RuntimeInfo.Print(options); return 0; }
    using var pki = new PkiDirectory();
    var configuration = Security.Configuration(options, false, pki.Path);
    await configuration.ValidateAsync(ApplicationType.Client);
    string policy = options.Security == "None" ? SecurityPolicies.None : SecurityPolicies.Basic256Sha256;
    var mode = options.Security == "None" ? MessageSecurityMode.None : MessageSecurityMode.SignAndEncrypt;
    using var discovery = await DiscoveryClient.CreateAsync(configuration, new Uri(options.Endpoint), EndpointConfiguration.Create(configuration));
    var endpoints = await discovery.GetEndpointsAsync(null);
    var endpoint = endpoints.FirstOrDefault(candidate => candidate.SecurityPolicyUri == policy && candidate.SecurityMode == mode
        && new Uri(candidate.EndpointUrl).Scheme == "opc.tcp")
        ?? throw new InvalidOperationException("Server does not offer the requested security policy and mode");
    // Use the requested host, as CoreClientUtils does, while retaining certificate
    // domain and application-URI validation during session creation.
    var address = new Uri(options.Endpoint);
    endpoint.EndpointUrl = new UriBuilder(endpoint.EndpointUrl) { Host = address.Host, Port = address.Port }.ToString();
    var configuredEndpoint = new ConfiguredEndpoint(null, endpoint, EndpointConfiguration.Create(configuration));
    using var session = await new DefaultSessionFactory(Security.Telemetry).CreateAsync(configuration,
        configuredEndpoint, false, true, "benchmark", 60_000, new UserIdentity(), null);
    session.KeepAliveInterval = Contract.RequestTimeout;
    session.OperationTimeout = Contract.RequestTimeout;
    Console.Error.WriteLine($"endpoint: {endpoint.SecurityPolicyUri} {endpoint.SecurityMode}");
    var workload = new Workload(session, options);
    long checksum = options.Mode == "async" ? await workload.RunAsync(options.Warmup) : workload.RunSync(options.Warmup);
    Console.WriteLine("O6_BENCHMARK_READY");
    Console.Out.Flush();
    if (Console.ReadLine() is null) throw new EndOfStreamException("benchmark barrier closed before release");
    long start = MonotonicClock.Now();
    checksum ^= options.Mode == "async" ? await workload.RunAsync(options.Iterations) : workload.RunSync(options.Iterations);
    long end = MonotonicClock.Now();
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        benchmark = options.Operation + "/" + options.Mode,
        implementation = "ua-dotnet", operations = options.Iterations,
        values_per_operation = options.BatchSize * options.ArraySize,
        start_ns = start, end_ns = end, checksum = checksum.ToString()
    }));
    Console.Out.Flush();
    await session.CloseAsync(5_000, true);
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error);
    return 1;
}
