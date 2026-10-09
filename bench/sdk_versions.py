#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""Compare each pinned server SDK with its latest upstream release, and bump a pin.

``python -m bench.sdk_versions check [--json]`` lists pinned and latest versions.
``python -m bench.sdk_versions bump <sdk> [<version>]`` rewrites that SDK's pins
(the latest release by default) so a normal build and run measure it.

open62541 and ua-dotnet are reported but never bumped automatically: the
open62541 submodule also builds the native client every suite measures with,
so moving it shifts every server's baseline at once, and a ua-dotnet bump needs
its NuGet lock files regenerated with the pinned .NET SDK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = ROOT / "common" / "node"
REQUIREMENTS = ROOT / "requirements.txt"
MILO_POM = ROOT / "common" / "milo" / "pom.xml"
GO_DIR = ROOT / "common" / "gopcua"
SDK_TOOLCHAINS = ROOT / "common" / "sdk_toolchains.json"
DOTNET_TOOLCHAIN = ROOT / "common" / "dotnet" / "toolchain.json"


def _get(url: str, *, raw: bool = False):
    headers = {"User-Agent": "opcua-benchmarks-sdk-versions"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        body = response.read()
    return body if raw else json.loads(body)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _replace(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"{path.relative_to(ROOT)}: pin pattern {pattern!r} not found")
    path.write_text(updated, encoding="utf-8")


def _requirement(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}==(\S+)", REQUIREMENTS.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"requirements.txt has no {name}== pin")
    return match.group(1)


def _gitlink(path: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "HEAD", path], text=True).split()[2]


# --- node-opcua ---------------------------------------------------------------


def _node_pinned() -> str:
    return json.loads((NODE_DIR / "package.json").read_text())["dependencies"]["node-opcua"]


def _node_latest() -> str:
    return _get("https://registry.npmjs.org/node-opcua/latest")["version"]


def _node_bump(version: str) -> None:
    manifest = _get(f"https://registry.npmjs.org/node-opcua/{version}")
    _replace(NODE_DIR / "package.json", r'"node-opcua": "[^"]+"', f'"node-opcua": "{version}"')
    toolchain = NODE_DIR / "toolchain.json"
    _replace(toolchain, r'"package": "[^"]+"', f'"package": "{version}"')
    if manifest.get("gitHead"):
        _replace(toolchain, r'"source_commit": "[^"]+"', f'"source_commit": "{manifest["gitHead"]}"')
    _replace(NODE_DIR / "README.md", r"node-opcua \d+\.\d+\.\d+", f"node-opcua {version}")
    npm = json.loads(toolchain.read_text())["npm"]
    subprocess.run(
        ["npx", "--yes", f"npm@{npm}", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=NODE_DIR,
        check=True,
    )


# --- Python packages ------------------------------------------------------------


def _pypi_latest(name: str) -> Callable[[], str]:
    return lambda: _get(f"https://pypi.org/pypi/{name}/json")["info"]["version"]


def _pypi_bump(name: str) -> Callable[[str], None]:
    return lambda version: _replace(REQUIREMENTS, rf"^{re.escape(name)}==\S+", f"{name}=={version}")


# --- Eclipse Milo -------------------------------------------------------------


def _milo_pinned() -> str:
    return re.search(r"<milo\.version>([^<]+)</milo\.version>", MILO_POM.read_text()).group(1)


def _milo_latest() -> str:
    metadata = _get(
        "https://repo1.maven.org/maven2/org/eclipse/milo/milo-sdk-server/maven-metadata.xml", raw=True
    ).decode()
    return re.search(r"<release>([^<]+)</release>", metadata).group(1)


def _milo_bump(version: str) -> None:
    _replace(MILO_POM, r"<milo\.version>[^<]+</milo\.version>", f"<milo.version>{version}</milo.version>")
    _replace(SDK_TOOLCHAINS, r'"org\.eclipse\.milo:milo-sdk-server [^"]+"', f'"org.eclipse.milo:milo-sdk-server {version}"')
    _replace(ROOT / "common" / "SDK_SERVERS.md", r"Eclipse Milo \S+", f"Eclipse Milo {version}")


# --- gopcua -------------------------------------------------------------------


def _gopcua_pinned() -> str:
    return re.search(r"^require github\.com/gopcua/opcua (\S+)", (GO_DIR / "go.mod").read_text(), re.MULTILINE).group(1)


def _gopcua_latest() -> str:
    return _get("https://proxy.golang.org/github.com/gopcua/opcua/@latest")["Version"]


def _gopcua_bump(version: str) -> None:
    subprocess.run(["go", "get", f"github.com/gopcua/opcua@{version}"], cwd=GO_DIR, check=True)
    subprocess.run(["go", "mod", "tidy"], cwd=GO_DIR, check=True)
    _replace(SDK_TOOLCHAINS, r'"github\.com/gopcua/opcua v[^"]+"', f'"github.com/gopcua/opcua {version}"')
    _replace(ROOT / "common" / "SDK_SERVERS.md", r"gopcua v\d\S*", f"gopcua {version}")


# --- S2OPC --------------------------------------------------------------------


def _s2opc_pinned() -> str:
    return json.loads(SDK_TOOLCHAINS.read_text())["s2opc"]["sdk"].rsplit(" ", 1)[1]


def _s2opc_latest() -> str:
    tags = _get("https://gitlab.com/api/v4/projects/systerel%2FS2OPC/repository/tags?per_page=100")
    versions = [tag["name"].removeprefix("S2OPC_Toolkit_") for tag in tags if re.fullmatch(r"S2OPC_Toolkit_[\d.]+", tag["name"])]
    return max(versions, key=version_key)


def _s2opc_bump(version: str) -> None:
    url = f"https://gitlab.com/systerel/S2OPC/-/archive/S2OPC_Toolkit_{version}/S2OPC-S2OPC_Toolkit_{version}.tar.gz"
    digest = hashlib.sha256(_get(url, raw=True)).hexdigest()
    pins = json.loads(SDK_TOOLCHAINS.read_text())
    pins["s2opc"]["sdk"] = f"S2OPC Toolkit {version}"
    pins["s2opc"]["archives"]["s2opc"].update(url=url, sha256=digest)
    SDK_TOOLCHAINS.write_text(json.dumps(pins, indent=2) + "\n")
    _replace(ROOT / "common" / "SDK_SERVERS.md", r"S2OPC Toolkit [\d.]+", f"S2OPC Toolkit {version}")


# --- report-only SDKs ---------------------------------------------------------


def _open62541_pinned() -> str:
    return _gitlink("deps/open62541")[:12]


def _open62541_latest() -> str:
    return _get("https://api.github.com/repos/open62541/open62541/releases/latest")["tag_name"]


def _open62541_newer(pinned: str, latest: str) -> bool:
    """The pin is a commit; a release is newer when the pin is an ancestor of the release tag."""
    comparison = _get(f"https://api.github.com/repos/open62541/open62541/compare/{_gitlink('deps/open62541')}...{latest}")
    return comparison["status"] == "ahead"


def _dotnet_pinned() -> str:
    return json.loads(DOTNET_TOOLCHAIN.read_text())["source_version"]


def _dotnet_latest() -> str:
    return _get("https://api.github.com/repos/OPCFoundation/UA-.NETStandard/releases/latest")["tag_name"]


@dataclass(frozen=True)
class Sdk:
    name: str
    pinned: Callable[[], str]
    latest: Callable[[], str]
    bump: Callable[[str], None] | None
    note: str = ""
    newer: Callable[[str, str], bool] = lambda pinned, latest: version_key(latest) > version_key(pinned)


SDKS: dict[str, Sdk] = {
    sdk.name: sdk
    for sdk in (
        Sdk("node-opcua", _node_pinned, _node_latest, _node_bump),
        Sdk("asyncua", lambda: _requirement("asyncua"), _pypi_latest("asyncua"), _pypi_bump("asyncua")),
        Sdk("o6-python", lambda: _requirement("o6"), _pypi_latest("o6"), _pypi_bump("o6")),
        Sdk("milo", _milo_pinned, _milo_latest, _milo_bump),
        Sdk("gopcua", _gopcua_pinned, _gopcua_latest, _gopcua_bump),
        Sdk("s2opc", _s2opc_pinned, _s2opc_latest, _s2opc_bump),
        Sdk(
            "open62541",
            _open62541_pinned,
            _open62541_latest,
            None,
            "also the native client of every suite: bump by hand",
            _open62541_newer,
        ),
        Sdk("ua-dotnet", _dotnet_pinned, _dotnet_latest, None, "needs NuGet locks regenerated: bump by hand"),
    )
}


def check() -> list[dict]:
    rows = []
    for sdk in SDKS.values():
        row = {"sdk": sdk.name, "pinned": sdk.pinned(), "automatic": sdk.bump is not None, "note": sdk.note}
        try:
            row["latest"] = sdk.latest()
            row["outdated"] = sdk.newer(row["pinned"], row["latest"])
        except Exception as error:  # one unreachable registry must not hide the others
            row.update(latest=None, outdated=False, error=str(error))
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="List pinned and latest versions")
    check_parser.add_argument("--json", action="store_true")
    commands.add_parser("pinned", help="Print the pinned versions as JSON (no network)")
    bump_parser = commands.add_parser("bump", help="Rewrite one SDK's pins")
    bump_parser.add_argument("sdk", choices=[name for name, sdk in SDKS.items() if sdk.bump])
    bump_parser.add_argument("version", nargs="?")
    args = parser.parse_args(argv)

    if args.command == "pinned":
        print(json.dumps({name: sdk.pinned() for name, sdk in SDKS.items()}, indent=2))
        return 0
    if args.command == "bump":
        sdk = SDKS[args.sdk]
        version = args.version or sdk.latest()
        sdk.bump(version)
        print(f"{sdk.name} pinned to {version}")
        return 0
    rows = check()
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            state = "error: " + row["error"] if "error" in row else ("NEW" if row["outdated"] else "up to date")
            print(f"{row['sdk']:<11} {row['pinned']:<14} {str(row['latest']):<14} {state}  {row['note']}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
