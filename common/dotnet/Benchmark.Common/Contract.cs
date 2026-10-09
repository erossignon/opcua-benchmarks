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
