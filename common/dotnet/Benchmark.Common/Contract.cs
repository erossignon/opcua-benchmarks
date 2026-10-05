namespace Benchmark.Common;

public sealed class NodeCursor(uint seed)
{
    private uint state = seed;
    public int Next()
    {
        state = unchecked(1664525U * state + 1013904223U);
        return (int)(state % Contract.NodeCount);
    }
}

public static class Contract
{
    public const string Endpoint = "opc.tcp://127.0.0.1:4840";
    public const string Namespace = "urn:o6:benchmark:server";
    public const uint FirstNodeId = 1001;
    public const int NodeCount = 100;
    public const uint FirstArrayId = 2001;
    public const int RequestTimeout = 60_000;
    public const int MaxMessageSize = 64 * 1024 * 1024;
    public const int MaxArrayLength = 8_294_400;

    public static int[] ArrayPayload(int size) => Enumerable.Range(0, size).Select(i => i % 1000).ToArray();
    public static uint ArrayNodeId(int[] sizes, int size)
    {
        int index = Array.IndexOf(sizes, size);
        if (index < 0) throw new ArgumentException("Array size absent from --array-sizes");
        return FirstArrayId + (uint)index;
    }
}
