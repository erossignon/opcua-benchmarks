import { parseArgs } from 'node:util';
import { DEFAULT_ENDPOINT, MAX_ARRAY_SIZES } from './contract.mjs';

export function options(argv = process.argv.slice(2)) {
  const defaults = {
    profile: 'throughput', port: '4840', endpoint: DEFAULT_ENDPOINT, security: 'None',
    'array-sizes': '', iterations: '1000', warmup: '100', samples: '1', worker: 'sync',
    operation: 'read', 'max-outstanding': '1', seed: '1', 'batch-size': '1', 'array-size': '1',
    'max-clients': '10', 'max-batch': '10000',
  };
  const specification = Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, { type: 'string', default: value }]));
  for (const key of ['certificate', 'private-key', 'trust-certificate']) specification[key] = { type: 'string' };
  const { values } = parseArgs({ args: argv, options: specification });
  const result = Object.fromEntries(Object.entries(values).map(([key, value]) => [key.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value]));
  for (const key of ['port', 'iterations', 'warmup', 'samples', 'maxOutstanding', 'seed', 'batchSize', 'arraySize', 'maxClients', 'maxBatch']) {
    result[key] = Number(result[key]);
    if (!Number.isSafeInteger(result[key]) || result[key] < (['warmup', 'seed'].includes(key) ? 0 : 1)) throw new Error(`Invalid ${key}`);
  }
  result.arraySizes = result.arraySizes ? result.arraySizes.split(',').map(Number) : [];
  if (result.arraySizes.length > MAX_ARRAY_SIZES || result.arraySizes.some(n => !Number.isSafeInteger(n) || n < 2)) throw new Error('Invalid array sizes');
  if (!['throughput', 'server_limits'].includes(result.profile)) throw new Error('Invalid profile');
  if (!['None', 'Basic256Sha256'].includes(result.security)) throw new Error('Invalid security');
  if (!['sync', 'async'].includes(result.worker) || !['read', 'write'].includes(result.operation)) throw new Error('Invalid workload');
  if (result.samples !== 1) throw new Error('Workers require --samples 1');
  if (result.arraySize > 1 && result.batchSize > 1) throw new Error('Batched arrays are unsupported');
  return result;
}
