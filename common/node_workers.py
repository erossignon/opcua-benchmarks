# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.
"""Local Node workers, isolated runtime settings, and dependency provenance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "common/node"
OUTPUT = SOURCE / "build"
NODE = ROOT / "deps/nodejs/bin/node"
NPM = ROOT / "deps/nodejs/lib/node_modules/npm/bin/npm-cli.js"
PINS = json.loads((SOURCE / "toolchain.json").read_text())
BUILD_INSTRUCTION = "Run python3 -m bench.build --node."
MEMORY_POLICY = "old-space=min(4096MiB,50%-share); RSS-watchdog-20ms; no-RLIMIT_AS; estimate-v1"


def environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = (os.environ if base is None else base).copy()
    for key in list(env):
        if key.upper().startswith(("NODE", "NPM_", "DEBUG", "UV_", "V8_", "OPCUA")) or key in {
            "ARRAYLENGTH",
            "CURRENT_CPU",
            "DISPLAY_ASSERT",
            "IGNORE_SUBTLE_FROM_CRYPTO",
            "NO_CREATE_PRIVATEKEY",
            "OPENSSL_CONF",
            "RANDFILE",
            "VERBOSE",
        }:
            del env[key]
    state = ROOT / "deps/node-state"
    env.update(
        NPM_CONFIG_USERCONFIG=str(state / "npmrc"),
        NPM_CONFIG_GLOBALCONFIG="/dev/null",
        NPM_CONFIG_CACHE=str(state / "cache"),
        NPM_CONFIG_UPDATE_NOTIFIER="false",
        PATH=str(NODE.parent) + os.pathsep + env.get("PATH", ""),
    )
    return env


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_hash(folder: Path) -> str:
    if not folder.is_dir():
        raise ValueError(f"missing installation: {folder}")
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(folder)).encode())
            digest.update(file_hash(path).encode())
    return digest.hexdigest()


def input_fingerprint() -> str:
    files = [Path(__file__), ROOT / "bench/build_node.py"]
    files += list(SOURCE.glob("*.mjs")) + list(SOURCE.glob("*.json"))
    files += list((ROOT / "throughput/node").glob("*.mjs"))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_identity(node: Path = NODE) -> dict:
    info = json.loads(
        subprocess.check_output(
            [str(node), "-p", "JSON.stringify({versions:process.versions,arch:process.arch,platform:process.platform})"],
            env=environment(),
            text=True,
            timeout=30,
        )
    )
    for key in ("node", "uv", "openssl", "v8"):
        if info["versions"][key] != PINS[key]:
            raise ValueError(f"Node runtime {key} differs from {PINS[key]}")
    npm = node.parent.parent / "lib/node_modules/npm/package.json"
    info["npm"] = json.loads(npm.read_text())["version"]
    if info["npm"] != PINS["npm"]:
        raise ValueError("Unexpected npm version")
    return info


def runtime_info() -> dict:
    return json.loads(
        subprocess.check_output(
            [str(NODE), str(SOURCE / "runtime-info.mjs")],
            env=environment(),
            text=True,
            timeout=30,
        )
    )


def command(role: str, profile: str) -> list[str]:
    script = SOURCE / "server.mjs" if role == "server" else ROOT / "throughput/node/client.mjs"
    return [str(NODE), str(script), "--profile", profile]


def preflight(roles: set[str], profile: str) -> dict:
    try:
        manifest = json.loads((OUTPUT / "provenance.json").read_text())
        for required in (
            "inputs",
            "runtime",
            "runtime_sha256",
            "npm_sha256",
            "dependencies_sha256",
            "lock_sha256",
            "source_commit",
            "runtime_info",
            "lifecycle_policy",
        ):
            if required not in manifest:
                raise ValueError(f"incomplete setup manifest: missing {required}")
        if manifest["source_commit"] != PINS["source_commit"] or manifest["lifecycle_policy"] != "ignore-scripts":
            raise ValueError("setup provenance differs from the pinned release or lifecycle policy")
        if manifest["lock_sha256"] != file_hash(SOURCE / "package-lock.json"):
            raise ValueError("package lock changed")
        if manifest["inputs"] != input_fingerprint():
            raise ValueError("worker or setup inputs changed")
        if manifest["runtime_sha256"] != file_hash(NODE) or manifest["runtime"] != runtime_identity():
            raise ValueError("runtime changed")
        if manifest["npm_sha256"] != tree_hash(NPM.parents[1]):
            raise ValueError("bundled npm changed")
        if manifest["dependencies_sha256"] != tree_hash(SOURCE / "node_modules"):
            raise ValueError("installed dependencies changed")
        if manifest["runtime_info"].get("package") != PINS["package"] or manifest["runtime_info"] != runtime_info():
            raise ValueError("package identity changed")
        for role in roles:
            if not Path(command(role, profile)[1]).is_file():
                raise ValueError(f"missing {role} worker")
        return {
            **manifest,
            "profile": profile,
            "mode_semantics": "sync: sequential; async: continuously refilled window",
            "memory_policy": MEMORY_POLICY if profile == "throughput" else "oom-priority-only",
        }
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Node workers unavailable or stale: {error}. {BUILD_INSTRUCTION}") from error


def prepare_metadata(store, selected: bool, roles: set[str], profile: str, amend: bool, capacity: dict | None = None) -> None:
    rows = [
        r
        for r in store.results.values()
        if r.get("runs")
        and (
            r.get("config_key", {}).get("implementation") == "node-opcua"
            or "node-opcua" in r.get("config_key", {}).get("pair", "").split(":")
        )
    ]
    if not selected:
        for key in ("node_opcua", "node_opcua_diagnostics"):
            if key in store.stored_metadata:
                store.metadata[key] = store.stored_metadata[key]
        return
    current = preflight(roles, profile)
    if capacity is not None:
        current["throughput_capacity"] = capacity
    if rows and amend and store.stored_metadata.get("node_opcua") != current:
        raise RuntimeError("Existing Node samples have missing or incompatible provenance; use a new named run.")
    store.metadata["node_opcua"] = current


def resident_share(memory_bytes: int) -> int:
    from common.oom import WORKER_ADDRESS_SPACE_RESERVE_MB

    return max(0, memory_bytes - WORKER_ADDRESS_SPACE_RESERVE_MB * 1024**2)


def memory_command(command: list[str], memory_bytes: int) -> list[str]:
    if not memory_bytes:
        return command
    share = resident_share(memory_bytes)
    if share < 256 * 1024**2:
        raise RuntimeError("Node worker ran out of memory budget: requires at least 256 MiB resident share")
    return [command[0], f"--max-old-space-size={min(4096, share // (2 * 1024**2))}", *command[1:]]


def check_memory(array_size: int, batch_size: int, depth: int, memory_bytes: int, arrays: tuple[int, ...] = ()) -> None:
    if not memory_bytes:
        return
    share = resident_share(memory_bytes)
    # Count retained address-space values plus simultaneous payload, encoding,
    # encrypted chunks, and response copies. The watchdog also covers SDK/JIT growth.
    estimate = 192 * 1024**2 + sum(arrays) * 4 * 2 + depth * (array_size * 4 * 8 + batch_size * 2048)
    if estimate > share:
        raise RuntimeError(f"Node payload ran out of memory budget: estimated {estimate} resident bytes exceeds {share}")


class MemoryWatch:
    """Bound external buffers as well as V8 heaps; the parent owns worker termination."""

    def __init__(self, process, memory_bytes: int):
        self.process = process
        self.limit = resident_share(memory_bytes)
        self.peak = 0
        self.exceeded = False
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stopped.wait(0.02) and self.process.poll() is None:
            try:
                fields = (Path("/proc") / str(self.process.pid) / "statm").read_text().split()
                rss = int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
                self.peak = max(self.peak, rss)
                if self.limit and rss > self.limit:
                    self.exceeded = True
                    self.process.kill()
                    return
            except (OSError, ValueError, IndexError):
                return

    def close(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=1)
