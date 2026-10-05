#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""The single asyncua benchmark server shared by every suite."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from asyncua import Server, ua

from common.contract import (
    ARRAY_FIRST_NODE_ID,
    FIRST_NODE_ID,
    NODE_COUNT,
    SERVER_READY,
)
from common.servers._shared import parse_server_args


async def _run() -> None:
    args = parse_server_args()

    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{args.port}")
    await server.set_application_uri("urn:o6:benchmark:server")
    if args.security == "Basic256Sha256":
        await server.load_certificate(args.certificate)
        await server.load_private_key(args.private_key)
        server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    else:
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    for index in range(NODE_COUNT):
        variable = await server.nodes.objects.add_variable(
            ua.NodeId(FIRST_NODE_ID + index, 1),
            f"1:BenchmarkValue{index}",
            index,
            varianttype=ua.VariantType.Int32,
        )
        await variable.set_writable()

    for index, size in enumerate(args.array_sizes):
        payload = [element % 1000 for element in range(size)]
        variable = await server.nodes.objects.add_variable(
            ua.NodeId(ARRAY_FIRST_NODE_ID + index, 1),
            f"1:BenchmarkArray{size}",
            payload,
            varianttype=ua.VariantType.Int32,
        )
        await variable.set_writable()

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)

    async with server:
        print(
            f"asyncua {SERVER_READY} at opc.tcp://127.0.0.1:{args.port}",
            flush=True,
        )
        await stopped.wait()


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
