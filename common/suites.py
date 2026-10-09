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

"""Option definitions, validators, and registry for the benchmark suites."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal


ConfigKind = Literal["uniform", "varying"]


@dataclass(frozen=True)
class ConfigOption:
    """One declared per-suite configuration field."""

    kind: ConfigKind
    default: Any
    coerce: Callable[[str], Any]
    help: str


SUITE_OPTIONS: dict[str, dict[str, ConfigOption]] = {}


def walk_matrix(uniform: dict, varying: dict) -> Iterator[dict]:
    """Yield varying-axis combinations merged with uniform settings; skip empty axes."""
    axes = [(name, list(values)) for name, values in varying.items() if values]
    names = [name for name, _ in axes]
    for combination in itertools.product(*(values for _, values in axes)):
        yield {**dict(zip(names, combination)), **uniform}


# --- shared coerce callables ----------------------------------------------
#
# Each adapter takes a single-typed-value validator and turns it into the
# kind-specific ``coerce`` :class:`BenchDB.set_config` expects. ``uniform``
# parses the input as JSON and validates the typed value once; ``varying``
# parses the input as a JSON list and validates each element.


def uniform(validator: Callable[[Any], str]) -> Callable[[str], Any]:
    """Build a ``uniform`` ``coerce``: parse the input as JSON, validate, return typed value.

    ``validator`` is the same single-value callable the old Config-class
    spec used (returns ``True`` on accept, a string reason on reject), so
    a hand-written rule from a suite moves across unchanged.
    """
    def coerce(raw: str) -> Any:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON: {error}") from None
        verdict = validator(value)
        if verdict is not True:
            raise ValueError(verdict)
        return value
    return coerce


def varying(validator: Callable[[Any], str]) -> Callable[[str], Any]:
    """Build a ``varying`` ``coerce``: parse the input as JSON list, validate each element.

    Each element is JSON-encoded and round-tripped through ``coerce`` so
    an element validator that already wants a typed value (e.g. an int)
    does not have to learn JSON.
    """
    def coerce(raw: str) -> list[Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON: {error}") from None
        if not isinstance(value, list):
            raise ValueError("must be a JSON-encoded list")
        result: list[Any] = []
        for item in value:
            verdict = validator(item)
            if verdict is not True:
                raise ValueError(verdict)
            result.append(item)
        return result
    return coerce


# --- per-element validators ------------------------------------------------
#
# These are the ``coerce`` building blocks for the per-element value. Each
# takes a typed value and returns ``True`` on accept or a string reason on
# reject.


def one_of(*allowed: Any) -> Callable[[Any], Any]:
    """Accept one of ``allowed``; reject with the allowed set named."""
    allowed_list = list(allowed)

    def validate(value: Any) -> Any:
        if value in allowed_list:
            return True
        return f"must be one of {', '.join(repr(option) for option in allowed_list)}"

    return validate


def whole_number(minimum: int) -> Callable[[Any], Any]:
    """Accept a plain integer of at least ``minimum``.

    ``bool`` is rejected explicitly: it is a subclass of ``int``, so
    ``true`` would otherwise pass for 1 and land in a result row as a
    count.
    """
    def validate(value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"must be an integer of at least {minimum}"
        if value < minimum:
            return f"must be an integer of at least {minimum}"
        return True

    return validate


def positive_number(value: Any) -> Any:
    """Accept a positive real number (used for thresholds, ratios, fractions)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be a positive number"
    if value <= 0:
        return "must be a positive number"
    return True


def endpoint_url(value: Any) -> Any:
    """Accept an ``opc.tcp://...`` endpoint URL."""
    if not isinstance(value, str) or not value.startswith("opc.tcp://"):
        return "must be an opc.tcp:// endpoint"
    return True


def path_string(value: Any) -> Any:
    """Accept any non-empty string; reject everything else.

    The cross-field check (do the binaries the matrix asks for actually
    live on disk?) is left to the runner: at seeding time the user has
    not yet had a chance to build the binaries, and the OPTIONS coerce
    only owns the value-shape contract.
    """
    if not isinstance(value, str):
        return "must be a path string"
    return True


def rate_fraction(value: Any) -> Any:
    """Accept a rate fraction in ``(0, 1]`` or ``None`` (a no-fraction sentinel)."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be in (0, 1] or null"
    if not (0 < value <= 1):
        return "must be in (0, 1] or null"
    return True


def payload_token(value: Any) -> Any:
    """Accept a :class:`throughput.run.Payload` token string.

    Imported lazily because ``Payload`` lives in ``throughput.run``, and
    the OPTIONS module for ``throughput`` only is the one that needs it.
    """
    from throughput.run import Payload

    Payload.from_token(value, ())
    return True


# --- registry population --------------------------------------------------


def _populate() -> None:
    """Load configuration options for each supported suite."""
    import importlib

    for suite_name in (
        "throughput", "server_limits",
        "server_capacity", "subscription",
    ):
        try:
            module = importlib.import_module(f"{suite_name}.options")
        except ModuleNotFoundError:
            continue
        SUITE_OPTIONS[suite_name] = dict(module.OPTIONS)


_populate()
