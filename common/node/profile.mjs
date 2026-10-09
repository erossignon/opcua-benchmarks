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

import { BinaryStream, Variant, adjustLimitsWithParameters } from './sdk.mjs';

export const MESSAGE_BYTES = 64 * 1024 * 1024;
export const ARRAY_ELEMENTS = 3840 * 2160;
export const transport = { maxMessageSize: MESSAGE_BYTES, maxChunkCount: 8192, receiveBufferSize: 65536, sendBufferSize: 65536 };

export function throughputDecoders() {
  BinaryStream.maxByteStringLength = MESSAGE_BYTES;
  BinaryStream.maxArrayLength = Math.max(BinaryStream.maxArrayLength, ARRAY_ELEMENTS);
  // Int32 decoding uses Variant.maxTypedArrayLength and readBuffer, not readArrayBuffer.
  if (Variant.maxTypedArrayLength < ARRAY_ELEMENTS) throw new Error('Insufficient typed-array decoder limit');
}

export function serverProfile(options) {
  if (options.profile === 'server_limits') return {};
  throughputDecoders();
  return {
    maxConnectionsPerEndpoint: options.maxClients + 16,
    serverCapabilities: {
      maxSessions: options.maxClients + 16, maxArrayLength: ARRAY_ELEMENTS,
      maxByteStringLength: MESSAGE_BYTES,
      operationLimits: { maxNodesPerRead: Math.max(10000, options.maxBatch), maxNodesPerWrite: Math.max(10000, options.maxBatch) },
    },
    transportSettings: { adjustTransportLimits: hello => adjustLimitsWithParameters(hello, {
      minBufferSize: 8192, maxBufferSize: 65536,
      minMaxMessageSize: 131072, defaultMaxMessageSize: MESSAGE_BYTES, maxMaxMessageSize: MESSAGE_BYTES,
      minMaxChunkCount: 1, defaultMaxChunkCount: 8192, maxMaxChunkCount: 8192,
    }) },
  };
}

export function effectiveLimits(server) {
  return { capabilities: server.engine.serverCapabilities, maxConnectionsPerEndpoint: server.maxConnectionsPerEndpoint,
    typedArrayElements: Variant.maxTypedArrayLength, generalArrayElements: BinaryStream.maxArrayLength,
    byteStringBytes: BinaryStream.maxByteStringLength };
}
