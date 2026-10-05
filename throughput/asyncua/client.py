#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""asyncua benchmark client consumed by the cross-language benchmark runner."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python throughput/asyncua/client.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio

from asyncua import Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.sync import Client as SyncClient

from throughput.benchmark_common import (
    REQUEST_TIMEOUT_MS,
    NodeCursor,
    Options,
    array_payload,
    distinct_node_ids,
    emit_async_worker_result,
    emit_measurement,
    emit_worker_result,
    parse_options,
    write_values,
)

# asyncua expresses its per-request timeout in seconds.
REQUEST_TIMEOUT_S = REQUEST_TIMEOUT_MS / 1000


def client_kwargs() -> dict:
    """Keyword arguments shared by both asyncua client constructors.

    Both :class:`asyncua.Client` and :class:`asyncua.sync.Client` accept
    ``watchdog_intervall`` as a keyword (positional order differs between
    the two, which is why it is passed by name). The watchdog probes the
    server's ``ServerState`` node on a tick and disconnects the session
    on probe failure; the probe's timeout budget is
    ``min(session_timeout / 2, watchdog_intervall)``. Left at the upstream
    1.0 s default, the budget is shorter than a pipelined async read at
    ``--max-outstanding 32`` and the client disconnects itself on a
    healthy server. Tying the interval to :data:`REQUEST_TIMEOUT_S`
    keeps the probe budget aligned with the request timeout the rest of
    the harness is bounded by.
    """
    return {"timeout": REQUEST_TIMEOUT_S, "watchdog_intervall": REQUEST_TIMEOUT_S}


def int32_data_value(value: int) -> ua.DataValue:
    return ua.DataValue(ua.Variant(value, ua.VariantType.Int32))


def int32_array_data_value(values: list[int]) -> ua.DataValue:
    return ua.DataValue(ua.Variant(values, ua.VariantType.Int32))


class SyncReads:
    def __init__(self, nodes: list, cursor: NodeCursor) -> None:
        self.nodes = nodes
        self.cursor = cursor
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        nodes, advance = self.nodes, self.cursor.next
        checksum = self.checksum
        for _ in range(iterations):
            checksum += int(nodes[advance()].read_value())
        self.checksum = checksum
        return checksum


class SyncWrites:
    def __init__(self, nodes: list, values: list, numbers: list[int], cursor: NodeCursor) -> None:
        self.nodes = nodes
        self.values = values
        self.numbers = numbers
        self.cursor = cursor
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        nodes, values, numbers, advance = self.nodes, self.values, self.numbers, self.cursor.next
        checksum = self.checksum
        for _ in range(iterations):
            index = advance()
            nodes[index].write_value(values[index])
            checksum += numbers[index]
        self.checksum = checksum
        return checksum


class AsyncReads:
    def __init__(self, nodes: list, cursor: NodeCursor, max_outstanding: int) -> None:
        self.nodes = nodes
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task[int]] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    node = self.nodes[self.cursor.next()]
                    remaining -= 1
                    pending.add(asyncio.create_task(node.read_value()))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    self.checksum += int(task.result())
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class AsyncWrites:
    def __init__(
        self,
        nodes: list,
        values: list,
        numbers: list[int],
        cursor: NodeCursor,
        max_outstanding: int,
    ) -> None:
        self.nodes = nodes
        self.values = values
        self.numbers = numbers
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task[None]] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    index = self.cursor.next()
                    remaining -= 1
                    self.checksum += self.numbers[index]
                    pending.add(asyncio.create_task(self.nodes[index].write_value(self.values[index])))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class SyncBatchReads:
    """``batch_size`` scalar nodes in one Read service call."""

    def __init__(self, client: SyncClient, nodes: list, batch_size: int, cursor: NodeCursor) -> None:
        self.client = client
        self.nodes = nodes
        self.batch_size = batch_size
        self.cursor = cursor
        self.checksum = 0

    def batch(self) -> list:
        nodes, advance = self.nodes, self.cursor.next
        return [nodes[advance()] for _ in range(self.batch_size)]

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            values = self.client.read_values(self.batch())
            self.checksum += int(values[0]) + len(values)
        return self.checksum


class SyncBatchWrites:
    def __init__(self, client: SyncClient, nodes: list, values: list, batch_size: int, cursor: NodeCursor) -> None:
        self.client = client
        self.nodes = nodes
        self.values = values
        self.batch_size = batch_size
        self.cursor = cursor
        self.checksum = 0

    def batch(self) -> tuple[list, list]:
        nodes, values, advance = self.nodes, self.values, self.cursor.next
        targets, payload = [], []
        for _ in range(self.batch_size):
            index = advance()
            targets.append(nodes[index])
            payload.append(values[index])
        return targets, payload

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            targets, payload = self.batch()
            self.client.write_values(targets, payload)
            self.checksum += self.batch_size
        return self.checksum


class SyncArrayReads:
    """One array variable per Read service call."""

    def __init__(self, client: SyncClient, node: str) -> None:
        self.node = client.get_node(node)
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            value = self.node.read_value()
            self.checksum += int(value[0]) + len(value)
        return self.checksum


class SyncArrayWrites:
    def __init__(self, client: SyncClient, node: str, payload: list[int]) -> None:
        self.node = client.get_node(node)
        self.payload = payload
        self.value = int32_array_data_value(payload)
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            self.node.write_value(self.value)
            self.checksum += len(self.payload)
        return self.checksum


class AsyncBatchReads:
    def __init__(
        self,
        client: Client,
        nodes: list,
        batch_size: int,
        cursor: NodeCursor,
        max_outstanding: int,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.batch_size = batch_size
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    def batch(self) -> list:
        nodes, advance = self.nodes, self.cursor.next
        return [nodes[advance()] for _ in range(self.batch_size)]

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    pending.add(asyncio.create_task(self.client.read_values(self.batch())))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    values = task.result()
                    self.checksum += int(values[0]) + len(values)
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class AsyncBatchWrites:
    def __init__(
        self,
        client: Client,
        nodes: list,
        values: list,
        batch_size: int,
        cursor: NodeCursor,
        max_outstanding: int,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.values = values
        self.batch_size = batch_size
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    def batch(self) -> tuple[list, list]:
        nodes, values, advance = self.nodes, self.values, self.cursor.next
        targets, payload = [], []
        for _ in range(self.batch_size):
            index = advance()
            targets.append(nodes[index])
            payload.append(values[index])
        return targets, payload

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    self.checksum += self.batch_size
                    targets, payload = self.batch()
                    pending.add(asyncio.create_task(self.client.write_values(targets, payload)))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class AsyncArrayReads:
    def __init__(self, client: Client, node: str, max_outstanding: int) -> None:
        self.node = client.get_node(node)
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    pending.add(asyncio.create_task(self.node.read_value()))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    value = task.result()
                    self.checksum += int(value[0]) + len(value)
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class AsyncArrayWrites:
    def __init__(self, client: Client, node: str, payload: list[int], max_outstanding: int) -> None:
        self.node = client.get_node(node)
        self.payload = payload
        self.value = int32_array_data_value(payload)
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    self.checksum += len(self.payload)
                    pending.add(asyncio.create_task(self.node.write_value(self.value)))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


def scalar_nodes(client) -> tuple[list, list, list[int]]:
    """The NODE_COUNT scalar nodes, plus the DataValue each write stores there.

    Resolved once. The LCG picks positions in these tables at call time, so the
    worker's memory does not grow with the batch size or the sample count.
    """
    strings = distinct_node_ids()
    numbers = write_values(strings)
    return (
        [client.get_node(text) for text in strings],
        [int32_data_value(value) for value in numbers],
        numbers,
    )


def configure_sync_security(client: SyncClient, options: Options) -> None:
    client.application_uri = "urn:o6:benchmark:client"
    if options.security == "None":
        return
    assert options.certificate
    assert options.private_key
    assert options.trust_certificate
    client.set_security(
        SecurityPolicyBasic256Sha256,
        options.certificate,
        options.private_key,
        server_certificate=options.trust_certificate,
    )


async def configure_async_security(client: Client, options: Options) -> None:
    client.application_uri = "urn:o6:benchmark:client"
    if options.security == "None":
        return
    assert options.certificate
    assert options.private_key
    assert options.trust_certificate
    await client.set_security(
        SecurityPolicyBasic256Sha256,
        options.certificate,
        options.private_key,
        server_certificate=options.trust_certificate,
    )


def sync_operation(client: SyncClient, options: Options):
    """Pick the sync operation for this payload shape.

    Scalar access keeps the per-node call form so the batch-of-one number stays
    comparable with every result measured before batching existed.
    """
    if options.array_size > 1:
        node = options.array_node_id
        if options.operation == "write":
            return SyncArrayWrites(client, node, array_payload(options.array_size))
        return SyncArrayReads(client, node)
    nodes, values, numbers = scalar_nodes(client)
    cursor = NodeCursor(options.seed)
    if options.batch_size > 1:
        if options.operation == "write":
            return SyncBatchWrites(client, nodes, values, options.batch_size, cursor)
        return SyncBatchReads(client, nodes, options.batch_size, cursor)
    if options.operation == "write":
        return SyncWrites(nodes, values, numbers, cursor)
    return SyncReads(nodes, cursor)


def async_operation(client: Client, options: Options):
    if options.array_size > 1:
        node = options.array_node_id
        if options.operation == "write":
            return AsyncArrayWrites(client, node, array_payload(options.array_size), options.max_outstanding)
        return AsyncArrayReads(client, node, options.max_outstanding)
    nodes, values, numbers = scalar_nodes(client)
    cursor = NodeCursor(options.seed)
    if options.batch_size > 1:
        if options.operation == "write":
            return AsyncBatchWrites(client, nodes, values, options.batch_size, cursor, options.max_outstanding)
        return AsyncBatchReads(client, nodes, options.batch_size, cursor, options.max_outstanding)
    if options.operation == "write":
        return AsyncWrites(nodes, values, numbers, cursor, options.max_outstanding)
    return AsyncReads(nodes, cursor, options.max_outstanding)


def run_sync(options: Options) -> None:
    client = SyncClient(options.endpoint, **client_kwargs())
    configure_sync_security(client, options)
    with client:
        operation = sync_operation(client, options)
        if options.worker:
            emit_worker_result(
                f"client_{options.operation}_sync_concurrent",
                operation,
                options,
                implementation="asyncua/open62541",
            )
        else:
            emit_measurement(
                f"client_{options.operation}_sync",
                operation,
                options,
                implementation="asyncua/open62541",
            )


async def run_async(options: Options) -> None:
    client = Client(options.endpoint, **client_kwargs())
    await configure_async_security(client, options)
    async with client:
        operation = async_operation(client, options)
        await emit_async_worker_result(
            f"client_{options.operation}_async_concurrent",
            operation,
            options,
            implementation="asyncua/open62541",
        )


def main() -> int:
    options = parse_options(__doc__ or "asyncua OPC UA client benchmark")
    if options.worker == "async":
        asyncio.run(run_async(options))
    else:
        run_sync(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
