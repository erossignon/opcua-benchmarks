import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { nodesets } from './sdk.mjs';
import { defaultServerCapabilities } from 'node-opcua-server/dist/server_capabilities.js';
import { Variant, BinaryStream } from './sdk.mjs';
import { MESSAGE_BYTES, ARRAY_ELEMENTS, transport } from './profile.mjs';

const require = createRequire(import.meta.url);
const lock = JSON.parse(readFileSync(new URL('./package-lock.json', import.meta.url)));
const packages = Object.fromEntries(Object.entries(lock.packages).filter(([name]) => name).map(([name, p]) => [name, { version: p.version, integrity: p.integrity }]));
console.log(JSON.stringify({
  package: require('node-opcua/package.json').version,
  packages,
  defaults: { capabilities: defaultServerCapabilities, maxConnectionsPerEndpoint: 10,
    typedArrayElements: Variant.maxTypedArrayLength, generalArrayElements: BinaryStream.maxArrayLength,
    byteStringBytes: BinaryStream.maxByteStringLength },
  throughput: { messageBytes: MESSAGE_BYTES, arrayElements: ARRAY_ELEMENTS, transport,
    sessions: 'max-clients + 16', connections: 'max-clients + 16', readWriteOperations: 'max(10000,max-batch)' },
  nodeset: { name: 'Opc.Ua.NodeSet2.xml', sha256: createHash('sha256').update(readFileSync(nodesets.standard)).digest('hex') },
}));
