# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
#    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

"""Fresh scalar-counter servers for the two Python SDKs."""

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import sys
import time

from subscription.options import MAX_ITEMS


async def produce(nodes, write, start, end, period, measured_window=None, interval_counts=None):
    """Increment only after a successful local write; skip missed grid slots."""
    values = [0] * len(nodes)
    measured = [0] * len(nodes) if measured_window is not None else None
    interval_ns = (measured_window[1] - measured_window[0]) // len(interval_counts) if interval_counts is not None else None
    due = start
    while time.monotonic_ns() < end:
        now = time.monotonic_ns()
        if now < due:
            await asyncio.sleep((due - now) / 1e9)
            continue
        for index, node in enumerate(nodes):
            admission = time.monotonic_ns()
            if admission >= end:
                break
            value = values[index] + 1
            await write(node, value)
            values[index] = value
            if measured is not None and measured_window[0] <= admission < measured_window[1]:
                measured[index] += 1
                if interval_counts is not None:
                    interval_counts[(admission - measured_window[0]) // interval_ns] += 1
        due = start + ((time.monotonic_ns() - start) // period + 1) * period
        await asyncio.sleep(0)
    return measured if measured is not None else values


class ReadCounters:
    """Advance only on SDK reads inside the armed workload window."""

    def __init__(self, count, measure_server):
        self.values = [0] * count
        self.counts = [0] * count if measure_server else None
        self.start = self.end = 0
        self.interval_counts = None

    def arm(self, start, end, measured_start, measured_end, interval_ns):
        self.start, self.end = start, end
        self.measured_start, self.measured_end = measured_start, measured_end
        self.interval_ns = interval_ns
        if self.counts is not None:
            self.interval_counts = [0] * ((measured_end - measured_start) // interval_ns)

    def read(self, index):
        admission = time.monotonic_ns()
        if self.start <= admission < self.end:
            self.values[index] += 1
            if self.counts is not None and self.measured_start <= admission < self.measured_end:
                self.counts[index] += 1
                self.interval_counts[(admission - self.measured_start) // self.interval_ns] += 1
        return self.values[index]


async def control(server, nodes, write, args, emit, counters=None):
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    producer = None

    async def run(start, end, measured_start, measured_end, interval_ns):
        window = (measured_start, measured_end) if args.measurement == "server" else None
        intervals = [0] * ((measured_end - measured_start) // interval_ns) if window else None
        if counters is None:
            values = await produce(nodes, write, start, end, args.sampling_ms * 1_000_000, window, intervals)
        else:
            while time.monotonic_ns() < end:
                await asyncio.sleep((end - time.monotonic_ns()) / 1e9)
            values, intervals = counters.counts, counters.interval_counts
        if args.measurement == "server":
            emit(dict(event="result", counts=values, interval_counts=intervals))
        else:
            emit(dict(event="done"))

    try:
        async with server:
            emit(
                dict(
                    event="ready",
                    sdk=args.implementation,
                    version=importlib.metadata.version("o6" if args.implementation == "o6-python" else "asyncua"),
                    clock="CLOCK_MONOTONIC",
                    counter_source="read_callback" if counters is not None else "application_update",
                )
            )
            while True:
                # Surface producer failures immediately, even while awaiting a command.
                command = asyncio.create_task(reader.readline())
                pending = {command} | ({producer} if producer is not None and not producer.done() else set())
                await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if producer is not None and producer.done():
                    producer.result()
                line = await command
                if not line or line.strip() == b"quit":
                    break
                parts = line.split()
                if len(parts) != 6 or parts[0] != b"arm" or producer is not None:
                    raise ValueError("Invalid control")
                start, end, measured_start, measured_end, interval_ns = map(int, parts[1:])
                if start <= time.monotonic_ns() or not start <= measured_start < measured_end <= end:
                    raise ValueError("Missed arming deadline")
                if interval_ns <= 0 or (measured_end - measured_start) % interval_ns:
                    raise ValueError("Invalid publishing interval")
                if counters is not None:
                    counters.arm(start, end, measured_start, measured_end, interval_ns)
                emit(
                    dict(
                        event="armed",
                        start_ns=start,
                        end_ns=end,
                        measured_start_ns=measured_start,
                        measured_end_ns=measured_end,
                        interval_ns=interval_ns,
                    )
                )
                producer = asyncio.create_task(run(start, end, measured_start, measured_end, interval_ns))
    finally:
        if producer is not None:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        transport.close()


async def serve(args, emit, *, callback_driven=True):
    counters = None
    if args.implementation == "asyncua":
        from asyncua import Server, ua

        server = Server()
        await server.init()
        server.set_endpoint(f"opc.tcp://127.0.0.1:{args.port}")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        nodes = [
            await server.nodes.objects.add_variable(ua.NodeId(i + 1, 1), f"1:Counter{i}", ua.Variant(0, ua.VariantType.UInt64))
            for i in range(args.items)
        ]

        # asyncua 2.0.1 notifies on writes; it does not periodically sample read
        # callbacks. Keep its producer, and disclose this exception in reports.
        async def write(node, value):
            await node.write_value(ua.Variant(value, ua.VariantType.UInt64))

    else:
        import o6

        server = o6.Server(port=args.port, loop=asyncio.get_running_loop())
        # The public o6 package uses its built-in subscription limits.
        if callback_driven:
            counters = ReadCounters(args.items, args.measurement == "server")
        nodes = []
        for i in range(args.items):
            node = server.addVariable("Counter", server.objectsNode, o6.UInt64(0), nodeId=f"ns=1;i={i + 1}")
            node(value=o6.Double(1), attr="MinimumSamplingInterval")
            nodes.append(node)
            if counters is not None:

                def read(node, index=i, **kwargs):
                    return o6.StatusCode.GOOD, o6.UInt64(counters.read(index))

                server.implement(node, read=read)

        async def write(node, value):
            node(value=o6.UInt64(value))

    if counters is None:
        await control(server, nodes, write, args, emit)
    else:
        await control(server, nodes, write, args, emit, counters)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("implementation", choices=("asyncua", "o6-python"))
    parser.add_argument("port", type=int)
    parser.add_argument("items", type=int)
    parser.add_argument("sampling_ms", type=int)
    parser.add_argument("measurement", choices=("server", "client"))
    args = parser.parse_args()
    if not 1 <= args.items <= MAX_ITEMS:
        parser.error(f"items must be in 1..{MAX_ITEMS}")
    out = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    logging.basicConfig(level=logging.ERROR)

    def emit(value):
        print(json.dumps(value, allow_nan=False), file=out)

    try:
        asyncio.run(serve(args, emit))
    except Exception as error:
        emit(dict(event="error", message=str(error)))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
