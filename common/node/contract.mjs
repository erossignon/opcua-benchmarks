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

export const DEFAULT_ENDPOINT = 'opc.tcp://127.0.0.1:4840';
export const FIRST_NODE_ID = 1001;
export const NODE_COUNT = 100;
export const ARRAY_FIRST_NODE_ID = 2001;
export const MAX_ARRAY_SIZES = 16;
export const SERVER_READY = 'benchmark server ready';
export const REQUEST_TIMEOUT = 60_000;

export class NodeCursor {
  constructor(seed = 0) { this.state = seed >>> 0; }
  next() {
    this.state = (Math.imul(1664525, this.state) + 1013904223) >>> 0;
    return this.state % NODE_COUNT;
  }
}

export function arrayPayload(size) {
  return Int32Array.from({ length: size }, (_, i) => i % 1000);
}

export function arrayNodeId(sizes, size) {
  const index = sizes.indexOf(size);
  if (index < 0) throw new Error(`Array size ${size} is not exposed`);
  return ARRAY_FIRST_NODE_ID + index;
}

export function output(text) {
  return new Promise((resolve, reject) => process.stdout.write(text, error => error ? reject(error) : resolve()));
}

export function releaseBarrier(input = process.stdin) {
  let released = false;
  let resolve;
  let reject;
  const ready = new Promise((yes, no) => { resolve = yes; reject = no; });
  // Attach the rejection handler before setup; an early EOF must remain observable.
  ready.catch(() => {});
  const data = chunk => {
    if (chunk.toString().includes('\n')) { released = true; resolve(); cleanup(); }
  };
  const end = () => { if (!released) reject(new Error('stdin EOF before benchmark release')); cleanup(); };
  const cleanup = () => { input.off('data', data); input.off('end', end); input.pause(); };
  input.on('data', data);
  input.on('end', end);
  input.resume();
  ready.dispose = cleanup;
  return ready;
}
