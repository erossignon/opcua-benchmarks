# Subscription capacity

Measures application counter progress while varying monitored-item count.
open62541, o6-python, node-opcua and .NET increment UInt64 counters in SDK read
callbacks during the armed workload window. Subscription sampling drives these
callbacks. One open62541 client provides the same setup and measurement behavior
across server SDKs. asyncua retains periodic application writes because its
notifications are write-triggered.

```sh
python -m bench.subscription new bench.db
python -m bench.subscription config bench.db implementation open62541 o6-python asyncua
python -m bench.subscription sample 1 bench.db --name subscriptions-1
python -m bench.subscription show bench.db --name subscriptions-1
```

Defaults include Node and .NET; build their [optional workers](../README.md#setup)
or narrow the implementation list. Use `config bench.db --help` for all settings.

## Measurement

The client runs on one physical core (its hyperthread siblings stay idle) and the
server on every other CPU, for every SDK: a server that uses several threads (the
node-opcua fronts, .NET, the JVM) can use them, a single-threaded one cannot.

The `client` observer measures the latest counter delivered to the client.
The `server` observer counts read-callback increments (completed application
updates for asyncua). These observers run as separate searches. Callback-driven
SDKs advance on reads only; asyncua uses the requested sampling interval as its
application update period.
Publishing intervals must be at least as long as sampling intervals.

Each observation includes at least 1,000 expected increments and five publishing
intervals, with another 10% for warmup rounded to whole publishes. Therefore a
large sampling interval still requires a long observation even with few items.

The adaptive search starts at `items_start`, doubles passing counts toward
`items_max`, and refines the passing/failing bracket to 10% or one item. Each
candidate is measured once. Every item must achieve at least 99% of expected
progress to pass; passing the cap establishes only an "at least" bound.
Execution errors leave the search incomplete.

Subscriptions use reporting mode, queue size one, and discard-oldest. Intermediate
values may be replaced. asyncua is write-triggered and reports the publishing
interval as its revised sampling interval; its application update period still
matches the requested workload.

Reports include approximate confidence intervals for mean progress based on a
circular block bootstrap of publishing intervals. These are informational, not
confidence intervals for maximum capacity or the worst item, and do not trigger
repeat measurements.

Settings, source and worker fingerprints, and per-case targets are stored with
the named run. `--amend` continues compatible saved searches; changing the
workload or rebuilding workers can require a new run. Older subscription data
may remain viewable but is not directly comparable to this counter workload.

The public o6 package uses its built-in subscription limits. If the SDK revises
requested intervals or queue sizes, the case is recorded as a setup failure;
select supported intervals instead. Long o6 observations can also exceed the
evaluation package's runtime limit.

Native client and server event loops use `CLOCK_MONOTONIC`, matching the
measurement coordinator. Reports disclose each observation's counter source;
older application-update results are retained and labeled with their original
workload rather than converted into callback measurements.
