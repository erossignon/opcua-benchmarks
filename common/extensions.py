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

"""Register additional suites and build steps before importing the harness.

Extensions contain metadata and lazy callbacks only. The first call to
``suites()`` freezes registration so the scheduler, database and reports
cannot observe different sets of suites in the same process.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Suite:
    name: str
    default_samples: int = 3
    takes_sample_count: bool = True
    schedule_amend: bool = True
    run_parameters: tuple[str, ...] = ("enabled", "samples", "amend", "skip_failed")
    classify: Callable | None = None
    has_pending: Callable | None = None

    @property
    def sample_cli(self) -> str:
        count = f" {self.default_samples}" if self.takes_sample_count else ""
        return f"python -m bench.{self.name} sample{count}"


_suites = [
    Suite("throughput"),
    Suite("server_limits"),
    Suite("server_capacity"),
    Suite("subscription", default_samples=1, run_parameters=("enabled", "amend", "skip_failed")),
]
_projects: list[tuple[str, str, tuple[str, ...]]] = []
_build_steps: list[Callable[..., bool]] = []
_frozen = False
_project_root = Path(__file__).resolve().parents[1]


def _require_registration_open() -> None:
    if _frozen:
        raise RuntimeError("Register extensions before importing suite options, commands or the scheduler")


def register_suite(suite: Suite, *, before: str | None = None) -> None:
    """Add a suite with ``<name>.options`` and ``bench.<name>`` modules.

    ``classify(store)`` may return "complete", "partial", or "none" for
    custom report availability; by default results are complete and failures
    without results are partial. ``has_pending(database, store)`` detects
    additional work when amending a saved run. Both callbacks should import
    suite code lazily. ``run_parameters`` controls the scheduler option rows.

    Its CLI supplies ``REPORT_NAME``, ``cmd_show(namespace)`` and
    ``cmd_sample(db, samples, amend, skip_failed)``. For one-shot suites,
    set ``takes_sample_count=False`` and supply ``cmd_sample(db, amend)``.
    ``schedule_amend=False`` disables per-suite amend except when resuming
    an explicitly named run. ``before`` positions a suite ahead of an
    existing registration; by default it is appended.
    """
    _require_registration_open()
    if not suite.name.isidentifier() or suite.default_samples < 1:
        raise ValueError("A suite needs a Python module name and a positive default sample count")
    names = [entry.name for entry in _suites]
    if suite.name in names:
        raise ValueError(f"Suite {suite.name!r} is already registered")
    index = len(_suites) if before is None else names.index(before)
    _suites.insert(index, suite)


def register_c_project(source: Path, build_type: str, executables: tuple[str, ...]) -> None:
    """Build an extension's CMake project through ``python -m bench.build``."""
    _require_registration_open()
    project = (str(source.resolve()), build_type, tuple(executables))
    if any(path == project[0] for path, _, _ in _projects):
        raise ValueError(f"CMake project {source} is already registered")
    _projects.append(project)


def register_build_step(step: Callable[..., bool]) -> None:
    """Append a callback accepting ``verbose`` and ``force`` keyword arguments.

    Return True on success or False on failure. Import build dependencies
    inside the callback so listing suites never runs or imports a build.
    """
    _require_registration_open()
    if step in _build_steps:
        raise ValueError("Build step is already registered")
    _build_steps.append(step)


def suites() -> tuple[Suite, ...]:
    """Return the ordered suite declarations and close registration."""
    global _frozen
    _frozen = True
    return tuple(_suites)


def c_projects() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    suites()
    return tuple(_projects)


def build_steps() -> tuple[Callable[..., bool], ...]:
    suites()
    return tuple(_build_steps)


def set_project_root(root: Path) -> None:
    """Run module workers from the embedding checkout so its adapters are importable."""
    global _project_root
    _require_registration_open()
    _project_root = root.resolve()


def project_root() -> Path:
    """Return the worker working directory; source/build paths stay owned by each package."""
    return _project_root
