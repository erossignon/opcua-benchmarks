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
