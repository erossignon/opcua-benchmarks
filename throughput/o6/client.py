#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Python benchmark client consumed by the cross-language benchmark runner."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python throughput/o6/client.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio

import numpy as np

import o6

from common.contract import configure_client

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


class SyncReads:
    """One scalar node per Read service call — the single-value baseline."""

    def __init__(self, client: o6.Client, nodes: list[o6.NodeId], cursor: NodeCursor) -> None:
        self.client = client
        self.nodes = nodes
        self.cursor = cursor
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        read, nodes, advance = self.client.read, self.nodes, self.cursor.next
        checksum = self.checksum
        for _ in range(iterations):
            checksum += int(read(nodes[advance()]))
        self.checksum = checksum
        return checksum


class SyncWrites:
    def __init__(
        self,
        client: o6.Client,
        nodes: list[o6.NodeId],
        values: list[o6.Int32],
        numbers: list[int],
        cursor: NodeCursor,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.values = values
        self.numbers = numbers
        self.cursor = cursor
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        write, nodes, values = self.client.write, self.nodes, self.values
        numbers, advance = self.numbers, self.cursor.next
        checksum = self.checksum
        for _ in range(iterations):
            index = advance()
            write(nodes[index], values[index])
            checksum += numbers[index]
        self.checksum = checksum
        return checksum


class SyncBatchReads:
    """``batch_size`` scalar nodes in one Read service call."""

    def __init__(self, client: o6.Client, nodes: list[o6.NodeId], batch_size: int, cursor: NodeCursor) -> None:
        self.client = client
        self.nodes = nodes
        self.batch_size = batch_size
        self.cursor = cursor
        self.checksum = 0

    def batch(self) -> list[o6.NodeId]:
        nodes, advance = self.nodes, self.cursor.next
        return [nodes[advance()] for _ in range(self.batch_size)]

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            values = self.client.read(self.batch())
            self.checksum += int(values[0]) + len(values)
        return self.checksum


class SyncBatchWrites:
    def __init__(
        self,
        client: o6.Client,
        nodes: list[o6.NodeId],
        values: list[o6.Int32],
        batch_size: int,
        cursor: NodeCursor,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.values = values
        self.batch_size = batch_size
        self.cursor = cursor
        self.checksum = 0

    def batch(self) -> tuple[list[o6.NodeId], list[o6.Int32]]:
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
            self.client.write(targets, payload)
            self.checksum += self.batch_size
        return self.checksum


class SyncArrayReads:
    """One array variable per Read service call."""

    def __init__(self, client: o6.Client, node: o6.NodeId) -> None:
        self.client = client
        self.node = node
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            value = self.client.read(self.node)
            # O(1) so the checksum never competes with the transfer it measures.
            self.checksum += int(value[0]) + len(value)
        return self.checksum


class SyncArrayWrites:
    def __init__(self, client: o6.Client, node: o6.NodeId, payload: np.ndarray) -> None:
        self.client = client
        self.node = node
        self.payload = payload
        self.checksum = 0

    def __call__(self, iterations: int) -> int:
        for _ in range(iterations):
            self.client.write(self.node, self.payload)
            self.checksum += len(self.payload)
        return self.checksum


class AsyncReads:
    def __init__(
        self,
        client: o6.Client,
        nodes: list[o6.NodeId],
        cursor: NodeCursor,
        max_outstanding: int,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    node = self.nodes[self.cursor.next()]
                    remaining -= 1
                    pending.add(asyncio.create_task(self.client.read(node)))
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
        client: o6.Client,
        nodes: list[o6.NodeId],
        values: list[o6.Int32],
        numbers: list[int],
        cursor: NodeCursor,
        max_outstanding: int,
    ) -> None:
        self.client = client
        self.nodes = nodes
        self.values = values
        self.numbers = numbers
        self.cursor = cursor
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    index = self.cursor.next()
                    remaining -= 1
                    self.checksum += self.numbers[index]
                    pending.add(asyncio.create_task(self.client.write(self.nodes[index], self.values[index])))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


class AsyncBatchReads:
    """Pipelined batched reads: up to ``max_outstanding`` Read calls in flight."""

    def __init__(
        self,
        client: o6.Client,
        nodes: list[o6.NodeId],
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

    def batch(self) -> list[o6.NodeId]:
        nodes, advance = self.nodes, self.cursor.next
        return [nodes[advance()] for _ in range(self.batch_size)]

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    pending.add(asyncio.create_task(self.client.read(self.batch())))
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
        client: o6.Client,
        nodes: list[o6.NodeId],
        values: list[o6.Int32],
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

    def batch(self) -> tuple[list[o6.NodeId], list[o6.Int32]]:
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
                    pending.add(asyncio.create_task(self.client.write(targets, payload)))
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
    def __init__(self, client: o6.Client, node: o6.NodeId, max_outstanding: int) -> None:
        self.client = client
        self.node = node
        self.max_outstanding = max_outstanding
        self.checksum = 0

    async def __call__(self, iterations: int) -> int:
        remaining = iterations
        pending: set[asyncio.Task] = set()
        try:
            while remaining or pending:
                while remaining and len(pending) < self.max_outstanding:
                    remaining -= 1
                    pending.add(asyncio.create_task(self.client.read(self.node)))
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
    def __init__(self, client: o6.Client, node: o6.NodeId, payload: np.ndarray, max_outstanding: int) -> None:
        self.client = client
        self.node = node
        self.payload = payload
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
                    pending.add(asyncio.create_task(self.client.write(self.node, self.payload)))
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.checksum


def scalar_nodes() -> tuple[list[o6.NodeId], list[o6.Int32], list[int]]:
    """The NODE_COUNT scalar nodes, plus the Int32 each write stores there.

    Resolved once. The LCG picks positions in these tables at call time, so the
    worker's memory does not grow with the batch size or the sample count.
    """
    strings = distinct_node_ids()
    numbers = write_values(strings)
    return (
        [o6.NodeId(text) for text in strings],
        [o6.Int32(value) for value in numbers],
        numbers,
    )


def client_options(options: Options) -> dict[str, object]:
    if options.security == "None":
        return {}
    return {
        "certificate": options.certificate,
        "privateKey": options.private_key,
        "trustList": [options.trust_certificate],
        "securityMode": o6.SecurityMode.SIGN_AND_ENCRYPT,
        "securityPolicy": o6.SecurityPolicy.BASIC256SHA256,
        "applicationUri": "urn:o6:benchmark:client",
    }


def sync_operation(client: o6.Client, options: Options):
    """Pick the sync operation for this payload shape.

    Scalar access keeps the single-target call form so the batch-of-one number
    stays comparable with every result measured before batching existed.
    """
    if options.array_size > 1:
        node = o6.NodeId(options.array_node_id)
        if options.operation == "write":
            return SyncArrayWrites(client, node, np.asarray(array_payload(options.array_size), dtype=np.int32))
        return SyncArrayReads(client, node)
    nodes, values, numbers = scalar_nodes()
    cursor = NodeCursor(options.seed)
    if options.batch_size > 1:
        if options.operation == "write":
            return SyncBatchWrites(client, nodes, values, options.batch_size, cursor)
        return SyncBatchReads(client, nodes, options.batch_size, cursor)
    if options.operation == "write":
        return SyncWrites(client, nodes, values, numbers, cursor)
    return SyncReads(client, nodes, cursor)


def async_operation(client: o6.Client, options: Options):
    if options.array_size > 1:
        node = o6.NodeId(options.array_node_id)
        if options.operation == "write":
            return AsyncArrayWrites(
                client,
                node,
                np.asarray(array_payload(options.array_size), dtype=np.int32),
                options.max_outstanding,
            )
        return AsyncArrayReads(client, node, options.max_outstanding)
    nodes, values, numbers = scalar_nodes()
    cursor = NodeCursor(options.seed)
    if options.batch_size > 1:
        if options.operation == "write":
            return AsyncBatchWrites(client, nodes, values, options.batch_size, cursor, options.max_outstanding)
        return AsyncBatchReads(client, nodes, options.batch_size, cursor, options.max_outstanding)
    if options.operation == "write":
        return AsyncWrites(client, nodes, values, numbers, cursor, options.max_outstanding)
    return AsyncReads(client, nodes, cursor, options.max_outstanding)


def run_sync(options: Options) -> None:
    client = o6.Client(options.endpoint, **client_options(options))
    client.config.timeout = REQUEST_TIMEOUT_MS
    with client:
        operation = sync_operation(client, options)
        if options.worker:
            emit_worker_result(f"client_{options.operation}_sync_concurrent", operation, options)
        else:
            emit_measurement(f"client_{options.operation}_sync", operation, options)


async def run_async(options: Options) -> None:
    client = o6.Client(options.endpoint, **client_options(options))
    client.config.timeout = REQUEST_TIMEOUT_MS
    # Admit exactly the pipeline depth this run was asked for. Left at its
    # default the client refuses the call past its limit with
    # BadTooManyOperations, which would measure that refusal rather than the
    # server; the assignment has to happen before connecting. The fix is now
    # shared with the open62541 C client (common/contract.{h,py}).
    configure_client(client, options.max_outstanding)
    async with client:
        operation = async_operation(client, options)
        await emit_async_worker_result(f"client_{options.operation}_async_concurrent", operation, options)


def main() -> int:
    options = parse_options(__doc__ or "Python OPC UA client benchmark")
    if options.worker == "async":
        asyncio.run(run_async(options))
    else:
        run_sync(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
