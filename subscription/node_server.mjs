import { createInterface } from 'node:readline';
import { OPCUAServer, DataType, DataValue, Variant, VariantArrayType, nodesets, RegisterServerMethod,
  MonitoredItem, Subscription } from '../common/node/sdk.mjs';
import { security } from '../common/node/security.mjs';

const [portText, itemsText, samplingText, measurement] = process.argv.slice(2);
const port = Number(portText), items = Number(itemsText), sampling = Number(samplingText);
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
console.log = console.info = console.warn = console.error;
const now = () => process.hrtime.bigint(); // Linux CLOCK_MONOTONIC, shared with the coordinator.
let timer, server, identity;
try {
  if (!['server', 'client'].includes(measurement) || !Number.isInteger(items) || items < 1 || items > 1048576)
    throw new Error('Invalid worker arguments');
  identity = await security({ security: 'None' }, 'server');
  MonitoredItem.minimumSamplingInterval = 1;
  Subscription.minimumPublishingInterval = 1;
  Subscription.maxNotificationPerPublishHighLimit = 0xffffffff;
  server = new OPCUAServer({
    port, host: '127.0.0.1', hostname: '127.0.0.1', resourcePath: '',
    nodeset_filename: [nodesets.standard], allowAnonymous: true,
    serverInfo: { applicationUri: 'urn:o6:benchmark:server', productUri: 'urn:o6:benchmark',
      applicationName: { text: 'Counter benchmark' } },
    registerServerMethod: RegisterServerMethod.HIDDEN,
    securityModes: [identity.mode], securityPolicies: [identity.policy],
    serverCertificateManager: identity.manager, userCertificateManager: identity.manager,
    certificateFile: identity.certificateFile, privateKeyFile: identity.privateKeyFile,
    serverCapabilities: { maxSessions: 1, maxSubscriptions: 1, minSupportedSampleRate: 1,
      maxMonitoredItems: items, maxMonitoredItemsPerSubscription: items, maxMonitoredItemsQueueSize: 1,
      operationLimits: { maxMonitoredItemsPerCall: 256 } },
  });
  await server.initialize();
  const namespace = server.engine.addressSpace.getOwnNamespace();
  if (namespace.index !== 1) throw new Error('Expected namespace 1');
  let start, end, measuredStart, measuredEnd, intervalNs, intervalCounts;
  const values = Array(items).fill(0);
  const measured = measurement === 'server' ? Array(items).fill(0) : null;
  for (let i = 0; i < items; i++) {
    namespace.addVariable({
      organizedBy: server.engine.addressSpace.rootFolder.objects, nodeId: `ns=1;i=${i + 1}`,
      browseName: `Counter${i}`, dataType: 'UInt64', valueRank: -1, minimumSamplingInterval: 1,
      // A refresh callback supplies one sample. A synchronous getter can also
      // run when the SDK reads back its cache after refreshing a Read request.
      value: { refreshFunc(callback) {
        const admission = now();
        if (start !== undefined && admission >= start && admission < end) {
          values[i]++;
          if (measured && admission >= measuredStart && admission < measuredEnd) {
            measured[i]++;
            intervalCounts[Number((admission - measuredStart) / intervalNs)]++;
          }
        }
        callback(null, new DataValue({ sourceTimestamp: new Date(),
          value: new Variant({ dataType: DataType.UInt64, arrayType: VariantArrayType.Scalar,
            value: [Math.floor(values[i] / 0x100000000), values[i] >>> 0] }) }));
      } },
    });
  }
  await server.start();
  emit({ event: 'ready', sdk: 'node-opcua', runtime: process.version, clock: 'CLOCK_MONOTONIC',
    counter_source: 'read_callback' });
  function finish() {
    if (now() < end) {
      timer = setTimeout(finish, Math.max(1, Math.ceil(Number(end - now()) / 1e6)));
      return;
    }
    emit(measurement === 'server' ? { event: 'result', counts: measured, interval_counts: intervalCounts } : { event: 'done' });
  }
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of input) {
    if (line === 'quit') break;
    const parts = line.split(' ');
    if (parts.length !== 6 || parts[0] !== 'arm' || start !== undefined) throw new Error('Invalid control');
    start = BigInt(parts[1]); end = BigInt(parts[2]);
    measuredStart = BigInt(parts[3]); measuredEnd = BigInt(parts[4]);
    intervalNs = BigInt(parts[5]);
    if (start <= now() || measuredStart < start || measuredEnd <= measuredStart || end < measuredEnd)
      throw new Error('Missed arming deadline');
    if (intervalNs <= 0n || (measuredEnd - measuredStart) % intervalNs !== 0n) throw new Error('Invalid publishing interval');
    intervalCounts = measured ? Array(Number((measuredEnd - measuredStart) / intervalNs)).fill(0) : null;
    emit({ event: 'armed', start_ns: String(start), end_ns: String(end),
      measured_start_ns: String(measuredStart), measured_end_ns: String(measuredEnd), interval_ns: String(intervalNs) });
    finish();
  }
} catch (error) {
  emit({ event: 'error', message: String(error) });
  process.exitCode = 2;
} finally {
  clearTimeout(timer);
  process.stdin.destroy();
  if (server) await server.shutdown(0);
  if (identity) await identity.dispose();
}
