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

import { OPCUAServer, DataType, Variant, VariantArrayType, StatusCodes, nodesets, RegisterServerMethod } from './sdk.mjs';
import { options } from './options.mjs';
import { security } from './security.mjs';
import { serverProfile, effectiveLimits } from './profile.mjs';
import { FIRST_NODE_ID, NODE_COUNT, ARRAY_FIRST_NODE_ID, SERVER_READY, arrayPayload, output } from './contract.mjs';

const o = options();
const identity = await security(o, 'server');
const server = new OPCUAServer({
  port: o.port, host: '127.0.0.1', hostname: '127.0.0.1', resourcePath: '', nodeset_filename: [nodesets.standard],
  serverInfo: { applicationUri: 'urn:o6:benchmark:server', productUri: 'urn:o6:benchmark', applicationName: { text: 'o6 benchmark server' } },
  allowAnonymous: true, registerServerMethod: RegisterServerMethod.HIDDEN,
  securityModes: [identity.mode], securityPolicies: [identity.policy],
  serverCertificateManager: identity.manager, userCertificateManager: identity.manager,
  certificateFile: identity.certificateFile, privateKeyFile: identity.privateKeyFile,
  ...serverProfile(o),
});
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  const timeout = setTimeout(() => process.exit(1), 8000);
  timeout.unref();
  try { await server.shutdown(0); } finally { await identity.dispose(); clearTimeout(timeout); }
}
process.on('SIGINT', () => stop().catch(error => { console.error(error); process.exitCode = 1; }));
process.on('SIGTERM', () => stop().catch(error => { console.error(error); process.exitCode = 1; }));
try {
  await server.initialize();
  const namespace = server.engine.addressSpace.getOwnNamespace();
  if (namespace.index !== 1) throw new Error(`Benchmark namespace must be 1, got ${namespace.index}`);
  function variable(id, initial, array) {
    let value = new Variant({ dataType: DataType.Int32, arrayType: array ? VariantArrayType.Array : VariantArrayType.Scalar, value: initial });
    namespace.addVariable({ organizedBy: server.engine.addressSpace.rootFolder.objects,
      nodeId: `ns=1;i=${id}`, browseName: `Benchmark${id}`, dataType: 'Int32',
      valueRank: array ? 1 : -1, arrayDimensions: array ? [initial.length] : undefined,
      accessLevel: 'CurrentRead | CurrentWrite', userAccessLevel: 'CurrentRead | CurrentWrite', minimumSamplingInterval: 1,
      value: { get: () => value, set: incoming => {
        if (incoming.dataType !== DataType.Int32 || incoming.arrayType !== value.arrayType
          || (array && (!(incoming.value instanceof Int32Array) || incoming.value.length !== initial.length))) return StatusCodes.BadTypeMismatch;
        value = incoming;
        return StatusCodes.Good;
      } },
    });
  }
  for (let i = 0; i < NODE_COUNT; i++) variable(FIRST_NODE_ID + i, i, false);
  o.arraySizes.forEach((size, i) => variable(ARRAY_FIRST_NODE_ID + i, arrayPayload(size), true));
  await server.start();
  console.error(JSON.stringify({ node_opcua_limits: effectiveLimits(server) }));
  await output(`${SERVER_READY}\n`);
} catch (error) {
  console.error(error);
  await stop();
  process.exitCode = 1;
}
