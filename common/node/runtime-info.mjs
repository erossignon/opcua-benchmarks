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
