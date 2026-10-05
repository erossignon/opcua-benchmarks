using Benchmark.Common;
using Opc.Ua;
using Opc.Ua.Client;

namespace Benchmark.Client;

public sealed class Workload
{
    private readonly ISession session;
    private readonly WorkerOptions options;
    private readonly NodeCursor cursor;
    private readonly ReadValueId[] reads;
    private readonly WriteValue[] writes;
    private long checksum;

    public Workload(ISession session, WorkerOptions options)
    {
        this.session = session;
        this.options = options;
        cursor = new NodeCursor(options.Seed);
        uint[] ids = options.ArraySize > 1
            ? [Contract.ArrayNodeId(options.ArraySizes, options.ArraySize)]
            : Enumerable.Range(0, Contract.NodeCount).Select(i => Contract.FirstNodeId + (uint)i).ToArray();
        reads = ids.Select(id => new ReadValueId { NodeId = new NodeId(id, 1), AttributeId = Attributes.Value }).ToArray();
        writes = ids.Select(id => new WriteValue
        {
            NodeId = new NodeId(id, 1), AttributeId = Attributes.Value,
            Value = new DataValue(new Variant(options.ArraySize > 1 ? (object)Contract.ArrayPayload(options.ArraySize) : (int)id))
        }).ToArray();
        // SessionClientBatched otherwise splits a service into multiple requests.
        session.OperationLimits.MaxNodesPerRead = 0;
        session.OperationLimits.MaxNodesPerWrite = 0;
    }

    private (ReadValueIdCollection Reads, WriteValueCollection Writes, long WriteSum) Request()
    {
        var requestedReads = new ReadValueIdCollection(options.BatchSize);
        var requestedWrites = new WriteValueCollection(options.BatchSize);
        long sum = 0;
        for (int i = 0; i < options.BatchSize; i++)
        {
            int index = options.ArraySize > 1 ? 0 : cursor.Next();
            requestedReads.Add(reads[index]);
            requestedWrites.Add(writes[index]);
            sum += options.ArraySize > 1 ? options.ArraySize : (int)writes[index].Value.Value;
        }
        // Collections belong to this request until completion. Entries and
        // prebuilt payloads are immutable and may be shared across the window.
        return (requestedReads, requestedWrites, sum);
    }

    // Match the C driver's attribute helper for scalars and raw services for payloads.
    private TimestampsToReturn ReadTimestamps => options.BatchSize == 1 && options.ArraySize == 1
        ? (options.Mode == "sync" ? TimestampsToReturn.Source : TimestampsToReturn.Neither)
        : TimestampsToReturn.Source;

    private long ValidateRead(ResponseHeader header, DataValueCollection results)
    {
        Check(header.ServiceResult);
        if (results.Count != options.BatchSize) throw new InvalidDataException("Read returned wrong result count");
        foreach (var value in results)
        {
            Check(value.StatusCode);
            if (options.ArraySize > 1)
            {
                if (value.Value is not int[] array || array.Length != options.ArraySize)
                    throw new InvalidDataException("Read returned wrong Int32 array shape");
            }
            else if (value.Value is not int) throw new InvalidDataException("Read returned a non-Int32 scalar");
        }
        if (options.ArraySize > 1) return ((int[])results[0].Value)[0] + (long)options.ArraySize;
        return (int)results[0].Value + (options.BatchSize > 1 ? options.BatchSize : 0L);
    }

    private long ValidateWrite(ResponseHeader header, StatusCodeCollection results, long sum)
    {
        Check(header.ServiceResult);
        if (results.Count != options.BatchSize) throw new InvalidDataException("Write returned wrong result count");
        foreach (var status in results) Check(status);
        return sum;
    }

    private static void Check(StatusCode status)
    {
        if (!StatusCode.IsGood(status)) throw new ServiceResultException(status);
    }

    public long RunSync(int count)
    {
        for (int i = 0; i < count; i++)
        {
            var request = Request();
            var header = new RequestHeader { TimeoutHint = Contract.RequestTimeout };
            if (options.Operation == "read")
            {
#pragma warning disable CS0618 // The sync experiment deliberately uses the direct blocking service API.
                var response = session.Read(header, 0, ReadTimestamps, request.Reads, out var results, out _);
#pragma warning restore CS0618
                checksum += ValidateRead(response, results);
            }
            else
            {
#pragma warning disable CS0618
                var response = session.Write(header, request.Writes, out var results, out _);
#pragma warning restore CS0618
                checksum += ValidateWrite(response, results, request.WriteSum);
            }
        }
        return checksum;
    }

    private async Task<long> CallAsync()
    {
        var request = Request();
        var header = new RequestHeader { TimeoutHint = Contract.RequestTimeout };
        if (options.Operation == "read")
        {
            var response = await session.ReadAsync(header, 0, ReadTimestamps, request.Reads, CancellationToken.None).ConfigureAwait(false);
            return ValidateRead(response.ResponseHeader, response.Results);
        }
        else
        {
            var response = await session.WriteAsync(header, request.Writes, CancellationToken.None).ConfigureAwait(false);
            return ValidateWrite(response.ResponseHeader, response.Results, request.WriteSum);
        }
    }

    public async Task<long> RunAsync(int count)
    {
        var pending = new List<Task<long>>(Math.Min(options.Depth, count));
        int issued = 0;
        try
        {
            while (issued < count || pending.Count > 0)
            {
                while (issued < count && pending.Count < options.Depth)
                {
                    pending.Add(CallAsync());
                    issued++;
                }
                var completed = await Task.WhenAny(pending).ConfigureAwait(false);
                pending.Remove(completed);
                checksum += await completed.ConfigureAwait(false);
            }
        }
        finally
        {
            // Failure cannot leave requests using a session that is being disposed.
            await Task.WhenAll(pending).ConfigureAwait(false);
        }
        return checksum;
    }
}
