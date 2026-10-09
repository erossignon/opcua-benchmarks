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

using Benchmark.Common;
using Opc.Ua;
using Opc.Ua.Server;

namespace Benchmark.Server;

public sealed class BenchmarkNodeManager : CustomNodeManager2
{
    private readonly int[] sizes;
    public BenchmarkNodeManager(IServerInternal server, ApplicationConfiguration configuration, int[] sizes)
        : base(server, configuration, server.Telemetry.CreateLogger<BenchmarkNodeManager>(), Contract.Namespace)
    {
        this.sizes = sizes;
    }

    public override void CreateAddressSpace(IDictionary<NodeId, IList<IReference>> externalReferences)
    {
        lock (Lock)
        {
            if (NamespaceIndex != 1) throw new InvalidOperationException($"Benchmark namespace must be 1, got {NamespaceIndex}");
            if (!externalReferences.TryGetValue(ObjectIds.ObjectsFolder, out var references))
                externalReferences[ObjectIds.ObjectsFolder] = references = new List<IReference>();
            for (int i = 0; i < Contract.NodeCount; i++)
                AddVariable(Contract.FirstNodeId + (uint)i, i, references);
            for (int i = 0; i < sizes.Length; i++)
                AddVariable(Contract.FirstArrayId + (uint)i, Contract.ArrayPayload(sizes[i]), references);
        }
    }

    private void AddVariable(uint id, object value, IList<IReference> references)
    {
        var node = new BaseDataVariableState(null)
        {
            NodeId = new NodeId(id, NamespaceIndex),
            BrowseName = new QualifiedName($"Value{id}", NamespaceIndex),
            DisplayName = $"Value{id}",
            TypeDefinitionId = VariableTypeIds.BaseDataVariableType,
            ReferenceTypeId = ReferenceTypeIds.Organizes,
            DataType = DataTypeIds.Int32,
            ValueRank = value is int[] ? ValueRanks.OneDimension : ValueRanks.Scalar,
            ArrayDimensions = value is int[] array ? new ReadOnlyList<uint>([(uint)array.Length]) : null,
            AccessLevel = AccessLevels.CurrentReadOrWrite,
            UserAccessLevel = AccessLevels.CurrentReadOrWrite,
            Value = value,
            StatusCode = StatusCodes.Good,
            Timestamp = DateTime.UtcNow
        };
        node.AddReference(ReferenceTypeIds.Organizes, true, ObjectIds.ObjectsFolder);
        references.Add(new NodeStateReference(ReferenceTypeIds.Organizes, false, node.NodeId));
        AddPredefinedNode(SystemContext, node);
    }
}
