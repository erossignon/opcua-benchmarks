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

"""Install the verified official runtime and locked public npm distribution."""

from __future__ import annotations

import ctypes.util
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from common import node_workers as workers


def install_runtime() -> None:
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in workers.PINS["archives"]:
        raise RuntimeError("Node bootstrap supports Linux x64 and arm64")
    for library in ("c", "stdc++", "gcc_s"):
        if not ctypes.util.find_library(library):
            raise RuntimeError(f"Missing Node native prerequisite: {library}")
    if workers.NODE.is_file():
        workers.runtime_identity()
        return
    archive = f"node-v{workers.PINS['node']}-linux-{'x64' if architecture == 'x86_64' else 'arm64'}"
    with tempfile.TemporaryDirectory(prefix="node-install-", dir=workers.ROOT / "deps") as tmp:
        downloaded = Path(tmp) / "runtime.tar.xz"
        with urllib.request.urlopen(
            f"https://nodejs.org/dist/v{workers.PINS['node']}/{archive}.tar.xz", timeout=120
        ) as response:
            with downloaded.open("wb") as target:
                shutil.copyfileobj(response, target)
        if workers.file_hash(downloaded) != workers.PINS["archives"][architecture]:
            raise RuntimeError("Official Node archive checksum mismatch")
        with tarfile.open(downloaded) as bundle:
            bundle.extractall(tmp, filter="data")
        unpacked = Path(tmp) / archive
        workers.runtime_identity(unpacked / "bin/node")
        if workers.NODE.parent.parent.exists():
            raise RuntimeError("Incomplete deps/nodejs installation; remove it before retrying")
        unpacked.rename(workers.NODE.parent.parent)


def build(*, verbose: bool, force: bool) -> bool:
    marker = workers.OUTPUT / "provenance.json"
    try:
        if not force:
            try:
                workers.preflight({"server", "client"}, "throughput")
                print("node-opcua: up to date.")
                return True
            except RuntimeError:
                pass
        marker.unlink(missing_ok=True)
        install_runtime()
        inputs = workers.input_fingerprint()
        workers.OUTPUT.mkdir(parents=True, exist_ok=True)
        state = workers.ROOT / "deps/node-state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "npmrc").write_text("registry=https://registry.npmjs.org/\n")
        command = [str(workers.NODE), str(workers.NPM), "ci", "--omit=dev", "--no-audit", "--no-fund", "--ignore-scripts"]
        result = subprocess.run(command, cwd=workers.SOURCE, env=workers.environment(), capture_output=not verbose, text=True)
        if result.returncode:
            raise RuntimeError(f"npm ci failed: {result.stdout or ''}{result.stderr or ''}")
        info = workers.runtime_info()
        if info["package"] != workers.PINS["package"]:
            raise RuntimeError("Installed node-opcua differs from the pinned public release")
        if inputs != workers.input_fingerprint():
            raise RuntimeError("Node setup inputs changed during installation")
        manifest = {
            "inputs": inputs,
            "runtime": workers.runtime_identity(),
            "runtime_sha256": workers.file_hash(workers.NODE),
            "npm_sha256": workers.tree_hash(workers.NPM.parents[1]),
            "dependencies_sha256": workers.tree_hash(workers.SOURCE / "node_modules"),
            "lock_sha256": workers.file_hash(workers.SOURCE / "package-lock.json"),
            "source_commit": workers.PINS["source_commit"],
            "runtime_info": info,
            "lifecycle_policy": "ignore-scripts",
        }
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        temporary.replace(marker)
        print("Built node-opcua (public npm distribution).")
        return True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        marker.unlink(missing_ok=True)
        print(f"Node setup failed: {error}")
        return False
