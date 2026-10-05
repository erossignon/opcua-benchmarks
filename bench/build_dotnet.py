# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.
"""Bootstrap the pinned official SDK and publish only the benchmark project graph."""

from __future__ import annotations

import ctypes.util
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from common import dotnet_workers as workers


def install_sdk() -> None:
    """Verify an archive before atomically exposing a complete local installation."""
    rid = {"x86_64": "linux-x64", "aarch64": "linux-arm64"}.get(platform.machine())
    if platform.system() != "Linux" or rid not in workers.PINS["archives"]:
        raise RuntimeError("The local SDK bootstrap currently supports Linux x64 and arm64")
    missing = [
        name
        for name in ("c", "gcc_s", "stdc++", "z", "ssl", "crypto", "icuuc", "gssapi_krb5")
        if not ctypes.util.find_library(name)
    ]
    if missing:
        raise RuntimeError("Missing .NET native prerequisites: " + ", ".join(missing))
    if workers.DOTNET.is_file():
        version = subprocess.check_output(
            [str(workers.DOTNET), "--version"], cwd=workers.SOURCE, env=workers.environment(), text=True
        ).strip()
        if version != workers.PINS["sdk"]:
            raise RuntimeError(f"Local SDK is {version}, expected {workers.PINS['sdk']}")
        return
    archive = workers.PINS["archives"][rid]
    with tempfile.TemporaryDirectory(prefix="dotnet-install-", dir=workers.ROOT / "deps") as tmp:
        downloaded = Path(tmp) / "sdk.tar.gz"
        with urllib.request.urlopen(archive["url"], timeout=120) as response, downloaded.open("wb") as destination:
            shutil.copyfileobj(response, destination)
        with downloaded.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha512").hexdigest()
        if checksum != archive["sha512"]:
            raise RuntimeError("Official .NET SDK archive checksum mismatch")
        unpacked = Path(tmp) / "sdk"
        with tarfile.open(downloaded) as bundle:
            bundle.extractall(unpacked, filter="data")
        subprocess.run(
            [str(unpacked / "dotnet"), "--list-runtimes"], check=True, env=workers.environment(), capture_output=True
        )
        if workers.DOTNET.parent.exists():
            raise RuntimeError(f"Incomplete SDK installation at {workers.DOTNET.parent}; remove it before retrying")
        unpacked.rename(workers.DOTNET.parent)


def build(*, verbose: bool, force: bool) -> bool:
    from bench.build import run_commands

    try:
        install_sdk()
        state = workers.source_state()
        if state["commit"] != workers.PINS["source_commit"]:
            raise RuntimeError(
                "deps/ua-dotnet is not at the pinned release commit; run git submodule update --init deps/ua-dotnet"
            )
        workers.OUTPUT.mkdir(parents=True, exist_ok=True)
        manifest_path = workers.OUTPUT / "provenance.json"
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        inputs = workers.input_fingerprint()
        force = force or previous.get("inputs") != inputs
        manifest_path.unlink(missing_ok=True)
        projects = {
            "server": workers.SOURCE / "Benchmark.Server/Benchmark.Server.csproj",
            "client": workers.ROOT / "throughput/dotnet/Benchmark.Client.csproj",
        }
        args = [str(workers.DOTNET)]
        overrides = [
            "-p:Configuration=Release",
            "-p:CustomTestTarget=net10.0",
            "-p:NuGetAudit=false",
            "-p:Dockerbuild=true",
            "-p:Version=1.5.378.176",
            "-p:AssemblyVersion=1.5.378.0",
            "-p:FileVersion=1.5.378.176",
            "-p:RestorePackagesWithLockFile=true",
            f"-p:DirectoryBuildTargetsPath={workers.SOURCE / 'Build.targets'}",
        ]
        if force:
            commands = [
                args + ["clean", str(project), "-c", "Release", "-f", "net10.0"] + overrides for project in projects.values()
            ]
            if run_commands(".NET clean", commands, cwd=workers.SOURCE, env=workers.environment(), verbose=verbose) is None:
                return False
        for role, project in projects.items():
            commands = [
                args
                + ["restore", str(project), "--locked-mode", "--configfile", str(workers.SOURCE / "NuGet.Config")]
                + overrides
            ]
            commands.append(
                args
                + ["publish", str(project), "--no-restore", "-c", "Release", "-f", "net10.0", "-o", str(workers.OUTPUT / role)]
                + overrides
            )
            if (
                run_commands(f".NET {role} (Release)", commands, cwd=workers.SOURCE, env=workers.environment(), verbose=verbose)
                is None
            ):
                return False
        artifacts = {
            role: {
                str(p.relative_to(workers.OUTPUT / role)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((workers.OUTPUT / role).rglob("*"))
                if p.is_file()
            }
            for role in projects
        }
        packages = {}
        for lock in sorted((workers.SOURCE / "locks").glob("*.json")):
            for dependencies in json.loads(lock.read_text())["dependencies"].values():
                for name, dependency in dependencies.items():
                    if "resolved" in dependency:
                        packages[name] = dependency["resolved"]
        if workers.input_fingerprint() != inputs:
            raise RuntimeError("Build inputs changed during compilation; rebuild before sampling")
        manifest_path.write_text(
            json.dumps(
                {
                    "source": state,
                    "source_version": workers.PINS["source_version"],
                    "sdk": workers.PINS["sdk"],
                    "configuration": "Release",
                    "architecture": platform.machine(),
                    "inputs": inputs,
                    "artifacts": artifacts,
                    "packages": packages,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        return True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f".NET build failed: {error}")
        return False
