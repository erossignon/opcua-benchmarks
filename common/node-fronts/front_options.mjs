// The OPCUAServerOptions of one front thread, built inside that thread: the same endpoint, identity,
// security and transport limits as the single-thread worker (common/node/server.mjs). The server-wide
// limits are the engine's (server.mjs). Each front gets its own temporary PKI folder: fronts must not
// share one. A session worker calls this too, only for monitored-item hooks: it gets none.
import { RegisterServerMethod } from '../node/sdk.mjs';
import { security } from '../node/security.mjs';
import { serverProfile } from '../node/profile.mjs';

export default async function frontServerOptions(o, thread) {
  if (thread?.front === -1) return {};
  const identity = await security(o, 'server');
  return {
    port: o.port, host: '127.0.0.1', hostname: '127.0.0.1', resourcePath: '',
    serverInfo: { applicationUri: 'urn:o6:benchmark:server', productUri: 'urn:o6:benchmark', applicationName: { text: 'o6 benchmark server' } },
    allowAnonymous: true, registerServerMethod: RegisterServerMethod.HIDDEN,
    securityModes: [identity.mode], securityPolicies: [identity.policy],
    serverCertificateManager: identity.manager, userCertificateManager: identity.manager,
    certificateFile: identity.certificateFile, privateKeyFile: identity.privateKeyFile,
    ...serverProfile(o),
  };
}
