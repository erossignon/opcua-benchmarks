import { OPCUAClient } from '../../common/node/sdk.mjs';
import { options } from '../../common/node/options.mjs';
import { security } from '../../common/node/security.mjs';
import { throughputDecoders, transport } from '../../common/node/profile.mjs';
import { output, releaseBarrier } from '../../common/node/contract.mjs';
import { Workload, sessionTransactions } from './workload.mjs';

const release = releaseBarrier();
const o = options();
throughputDecoders();
const identity = await security(o, 'client');
const client = OPCUAClient.create({ applicationName: 'o6 benchmark client', applicationUri: 'urn:o6:benchmark:client',
  clientCertificateManager: identity.manager, certificateFile: identity.certificateFile, privateKeyFile: identity.privateKeyFile,
  serverCertificate: identity.peer, securityMode: identity.mode, securityPolicy: identity.policy,
  endpointMustExist: true, connectionStrategy: { maxRetry: 0 }, keepSessionAlive: false,
  requestedSessionTimeout: 120000, defaultSecureTokenLifetime: 600000, transportSettings: transport,
});
let session;
let channelError;
client.on('connection_lost', () => { channelError = new Error('Measured channel lost; reconnection disabled'); });
try {
  await client.connect(o.endpoint);
  const endpoints = await client.getEndpoints();
  const requested = new URL(o.endpoint);
  const endpoint = endpoints.find(endpoint => {
    const advertised = new URL(endpoint.endpointUrl);
    return advertised.protocol === requested.protocol && advertised.hostname === requested.hostname
      && advertised.port === requested.port && (advertised.pathname || '/') === (requested.pathname || '/')
      && endpoint.securityMode === identity.mode && endpoint.securityPolicyUri === identity.policy;
  });
  if (!endpoint) throw new Error('No matching advertised endpoint and security policy');
  // The .NET server advertises a trailing slash. Reconnect to that exact URL
  // during setup so endpointMustExist remains strict on every stack.
  if (endpoint.endpointUrl !== o.endpoint) {
    await client.disconnect();
    await client.connect(endpoint.endpointUrl);
  }
  if (o.security !== 'None' && endpoint.server.applicationUri !== 'urn:o6:benchmark:server') throw new Error('Wrong endpoint application URI');
  session = await client.createSession();
  if (client.securityMode !== identity.mode || client.securityPolicy !== identity.policy) throw new Error('Unexpected negotiated security');
  const workload = new Workload(sessionTransactions(client, session), o);
  const warmup = await workload.run(o.warmup);
  await output('O6_BENCHMARK_READY\n');
  await release;
  if (channelError) throw channelError;
  const start = process.hrtime.bigint();
  const checksum = warmup ^ await workload.run(o.iterations);
  const end = process.hrtime.bigint();
  if (channelError) throw channelError;
  await output(JSON.stringify({ benchmark: `client_${o.operation}_${o.worker}_concurrent`, implementation: 'node-opcua',
    operations: o.iterations, values_per_operation: o.batchSize * o.arraySize,
    start_ns: start.toString(), end_ns: end.toString(), checksum: checksum.toString(),
    node_opcua_diagnostics: { ...process.memoryUsage(), maxRssBytes: process.resourceUsage().maxRSS * 1024 },
  }) + '\n');
} catch (error) { console.error(error); process.exitCode = 1; }
finally {
  release.dispose();
  try { if (session) await session.close(); } finally { await client.disconnect(); await identity.dispose(); }
}
