using System.Runtime.InteropServices;
using Benchmark.Common;
using Benchmark.Server;
using Opc.Ua;

try
{
    if (args.Length > 0 && args[0] == "--counter") return await CounterWorker.Run(args[1..]);
    var options = new WorkerOptions(args);
    if (options.RuntimeInfo) { RuntimeInfo.Print(options); return 0; }
    using var pki = new PkiDirectory();
    var config = Security.Configuration(options, true, pki.Path);
    await config.ValidateAsync(ApplicationType.Server);
    using var server = new BenchmarkServer(options);
    var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    using var interrupt = PosixSignalRegistration.Create(PosixSignal.SIGINT, context => { context.Cancel = true; stopped.TrySetResult(); });
    using var terminate = PosixSignalRegistration.Create(PosixSignal.SIGTERM, context => { context.Cancel = true; stopped.TrySetResult(); });
    await server.StartAsync(config);
    Console.WriteLine("benchmark server ready");
    Console.Out.Flush();
    await stopped.Task;
    await server.StopAsync();
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error);
    return 1;
}
