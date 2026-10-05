import {
  AttributeIds, DataType, DataValue, ReadRequest, ReadValueId, StatusCodes,
  TimestampsToReturn, Variant, VariantArrayType, WriteRequest, WriteValue, makeNodeId,
} from '../../common/node/sdk.mjs';
import { FIRST_NODE_ID, NODE_COUNT, NodeCursor, REQUEST_TIMEOUT, arrayNodeId, arrayPayload } from '../../common/node/contract.mjs';

export function good(status) {
  if (!status || status.value !== StatusCodes.Good.value) throw new Error(`Bad service/item status: ${status}`);
}

/** Session-scoped public dispatch without the SDK session wrapper's automatic replay. */
export function sessionTransactions(client, session) {
  return {
    performMessageTransaction(request, callback) {
      if (!session.authenticationToken) throw new Error('Measured session has no authentication token');
      request.requestHeader.authenticationToken = session.authenticationToken;
      client.performMessageTransaction(request, callback);
    },
  };
}

/** A continuously refilled window; failure stops issuing and drains every issued call. */
export async function pipeline(count, depth, call) {
  let issued = 0;
  let error;
  async function lane() {
    while (!error && issued < count) {
      const index = issued++;
      try { await call(index); } catch (failure) { error ??= failure; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(count, depth) }, lane));
  if (error) throw error;
}

export class Workload {
  constructor(session, options) {
    this.session = session;
    this.options = options;
    this.cursor = new NodeCursor(options.seed);
    this.checksum = 0n;
    const ids = options.arraySize > 1 ? [arrayNodeId(options.arraySizes, options.arraySize)]
      : Array.from({ length: NODE_COUNT }, (_, i) => FIRST_NODE_ID + i);
    this.nodes = ids.map(id => makeNodeId(id, 1));
    this.values = ids.map(id => options.arraySize > 1 ? arrayPayload(options.arraySize) : id);
  }

  request() {
    const o = this.options;
    const indices = Array.from({ length: o.batchSize }, () => o.arraySize > 1 ? 0 : this.cursor.next());
    const header = { timeoutHint: REQUEST_TIMEOUT };
    if (o.operation === 'read') {
      return { request: new ReadRequest({ requestHeader: header, maxAge: 0,
        timestampsToReturn: o.worker === 'async' && o.batchSize === 1 && o.arraySize === 1
          ? TimestampsToReturn.Neither : TimestampsToReturn.Source,
        nodesToRead: indices.map(i => new ReadValueId({ nodeId: this.nodes[i], attributeId: AttributeIds.Value })),
      }) };
    }
    return { sum: indices.reduce((sum, i) => sum + BigInt(o.arraySize > 1 ? o.arraySize : this.values[i]), 0n),
      request: new WriteRequest({ requestHeader: header,
        nodesToWrite: indices.map(i => new WriteValue({ nodeId: this.nodes[i], attributeId: AttributeIds.Value,
          value: new DataValue({ value: new Variant({ dataType: DataType.Int32,
            arrayType: o.arraySize > 1 ? VariantArrayType.Array : VariantArrayType.Scalar, value: this.values[i] }) }),
        })),
      }),
    };
  }

  async call() {
    const { request, sum } = this.request();
    const response = await new Promise((resolve, reject) => {
      this.session.performMessageTransaction(request, (error, result) => error ? reject(error) : resolve(result));
    });
    good(response.responseHeader.serviceResult);
    const o = this.options;
    if (response.results.length !== o.batchSize) throw new Error('Wrong service result count');
    let increment;
    if (o.operation === 'write') {
      response.results.forEach(good);
      increment = sum;
    } else {
      for (const result of response.results) {
        good(result.statusCode);
        const v = result.value;
        if (v.dataType !== DataType.Int32) throw new Error('Read returned non-Int32');
        if (o.arraySize > 1) {
          if (v.arrayType !== VariantArrayType.Array || !(v.value instanceof Int32Array) || v.value.length !== o.arraySize) throw new Error('Wrong Int32 array shape');
        } else if (v.arrayType !== VariantArrayType.Scalar || !Number.isInteger(v.value)) throw new Error('Wrong scalar shape');
      }
      increment = o.arraySize > 1 ? BigInt(response.results[0].value.value[0]) + BigInt(o.arraySize)
        : BigInt(response.results[0].value.value) + BigInt(o.batchSize > 1 ? o.batchSize : 0);
    }
    this.checksum = BigInt.asUintN(64, this.checksum + increment);
  }

  async run(count) {
    await pipeline(count, this.options.worker === 'sync' ? 1 : this.options.maxOutstanding, () => this.call());
    return this.checksum;
  }
}
