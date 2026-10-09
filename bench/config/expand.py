#!/usr/bin/env python3
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

"""Expand a schedule into per-suite sampling calls."""

from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from bench.config import SUITE_ORDER, Entry, Schedule, db_ready


@dataclass(frozen=True)
class SuiteInvocation:
    suite_name: str
    namespace: argparse.Namespace
    run: Callable[[], int] = field(compare=False)


@dataclass(frozen=True)
class EntryPlan:
    db: Optional[Path]
    start_delay_minutes: int
    invocations: tuple[SuiteInvocation, ...]
    pre_banners: tuple[str, ...] = ()


@lru_cache(maxsize=None)
def _module_for(suite_name: str):
    return importlib.import_module(f"bench.{suite_name}")


def _make_run(module, suite_name: str, db: Path, namespace: argparse.Namespace) -> Callable[[], int]:
    def _run(db=db, namespace=namespace, module=module) -> int:
        return module.cmd_sample(
            db,
            namespace.num_samples,
            namespace.amend,
            namespace.skip_failed,
        )
    return _run


def expand_schedule(schedule: Schedule) -> list[EntryPlan]:
    plans: list[EntryPlan] = []
    for entry in schedule.entries:
        invocations: list[SuiteInvocation] = []
        pre_banners: list[str] = []
        present = db_ready(entry.db)
        for name in SUITE_ORDER:
            config = entry.suite(name)
            if not config.enabled:
                continue
            if not present:
                continue
            amend = config.amend
            if not amend and config.skip_failed:
                amend = True
            namespace = argparse.Namespace(
                database=entry.db,
                num_samples=config.samples,
                amend=amend,
                skip_failed=config.skip_failed,
            )
            invocations.append(
                SuiteInvocation(
                    suite_name=name,
                    namespace=namespace,
                    run=_make_run(_module_for(name), name, entry.db, namespace),
                )
            )
        plans.append(
            EntryPlan(
                db=entry.db,
                start_delay_minutes=entry.start_delay_minutes,
                invocations=tuple(invocations),
                pre_banners=tuple(pre_banners),
            )
        )
    return plans
