#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""The single o6\\Python benchmark server shared by every suite."""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import o6

from common.contract import (
    ARRAY_FIRST_NODE_ID,
    FIRST_NODE_ID,
    NODE_COUNT,
    SERVER_READY,
)
from common.servers._shared import parse_server_args


def main() -> int:
    args = parse_server_args()

    security_options = {}
    if args.security == "Basic256Sha256":
        security_options = {
            "certificate": args.certificate,
            "privateKey": args.private_key,
            "trustList": [args.trust_certificate],
            "acceptAllCertificates": True,
            "applicationUri": "urn:o6:benchmark:server",
        }

    server = o6.Server(port=args.port, **security_options)
    if args.security == "Basic256Sha256":
        # setEncryption rebuilds the native config after the constructor's URI
        # assignment. Restore the identity before endpoints are advertised.
        server.config.applicationUri = "urn:o6:benchmark:server"
    for index in range(NODE_COUNT):
        server.addVariable(
            f"BenchmarkValue{index}",
            server.objectsNode,
            o6.Int32(index),
            nodeId=f"ns=1;i={FIRST_NODE_ID + index}",
            writable=True,
        )
    if args.array_sizes:
        # Lazy: numpy reserves ~700 MB on import, and a server with no
        # arrays should not pay for it (see common/oom.py).
        import numpy as np
        for index, size in enumerate(args.array_sizes):
            payload = [element % 1000 for element in range(size)]
            server.addVariable(
                f"BenchmarkArray{size}",
                server.objectsNode,
                np.asarray(payload, dtype=np.int32),
                nodeId=f"ns=1;i={ARRAY_FIRST_NODE_ID + index}",
                writable=True,
            )

    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server.start()
    print(f"Python {SERVER_READY} at opc.tcp://127.0.0.1:{args.port}", flush=True)
    try:
        stopped.wait()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
