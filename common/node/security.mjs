import { X509Certificate, createPrivateKey } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { isIP } from 'node:net';
import { OPCUACertificateManager, MessageSecurityMode, SecurityPolicy } from './sdk.mjs';

export function validateIdentity(bytes, role, host = '127.0.0.1') {
  const cert = new X509Certificate(bytes);
  const now = Date.now();
  if (now < Date.parse(cert.validFrom) || now > Date.parse(cert.validTo)) throw new Error('Certificate is expired or not yet valid');
  if (!(cert.subjectAltName || '').split(/,\s*/).includes(`URI:urn:o6:benchmark:${role}`)) throw new Error('Wrong certificate application URI');
  if (role === 'server' && !(isIP(host) ? cert.checkIP(host) : cert.checkHost(host))) throw new Error('Wrong certificate hostname');
  return cert;
}

export function privateKeyPem(bytes) {
  // The shared generator emits DER. Try the two explicit RSA container formats.
  for (const type of ['pkcs8', 'pkcs1']) {
    try { return createPrivateKey({ key: bytes, format: 'der', type }).export({ format: 'pem', type: 'pkcs8' }); }
    catch { /* Try the other supported encoding. */ }
  }
  throw new Error('Private key is neither PKCS#8 nor PKCS#1 DER');
}

export async function security(options, role) {
  const root = process.env.O6_BENCHMARK_PKI_ROOT;
  if (!root) throw new Error('O6_BENCHMARK_PKI_ROOT must be a runner-owned temporary directory');
  await mkdir(root, { recursive: true });
  const directory = await mkdtemp(join(root, `node-${role}-`));
  const manager = new OPCUACertificateManager({ rootFolder: directory, automaticallyAcceptUnknownCertificate: false });
  const dispose = async () => { await manager.dispose(); await rm(directory, { recursive: true, force: true }); };
  try {
    await manager.initialize();
    const certificateFile = join(directory, 'identity.pem');
    let privateKeyFile = manager.privateKey;
    let peer;
    if (options.security !== 'None') {
      if (!options.certificate || !options.privateKey || !options.trustCertificate) throw new Error('Encrypted workers require a certificate, key, and trusted peer');
      const own = validateIdentity(await readFile(options.certificate), role);
      peer = validateIdentity(await readFile(options.trustCertificate), role === 'server' ? 'client' : 'server', new URL(options.endpoint).hostname).raw;
      privateKeyFile = join(directory, 'identity-key.pem');
      await writeFile(certificateFile, own.toString());
      await writeFile(privateKeyFile, privateKeyPem(await readFile(options.privateKey)), { mode: 0o600 });
      await manager.trustCertificate(peer);
      const status = await manager.checkCertificate(peer);
      if (status.value !== 0) throw new Error(`Trusted peer rejected: ${status}`);
    } else {
      await manager.createSelfSignedCertificate({ outputFile: certificateFile,
        applicationUri: `urn:o6:benchmark:${role}`, subject: `/CN=o6 benchmark ${role}`,
        dns: ['localhost'], ip: ['127.0.0.1'], validity: 365,
      });
    }
    return { manager, certificateFile, privateKeyFile, peer, dispose,
      mode: options.security === 'None' ? MessageSecurityMode.None : MessageSecurityMode.SignAndEncrypt,
      policy: options.security === 'None' ? SecurityPolicy.None : SecurityPolicy.Basic256Sha256,
    };
  } catch (error) { await dispose(); throw error; }
}
