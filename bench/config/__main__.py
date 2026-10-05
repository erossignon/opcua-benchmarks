#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 o6 Automation GmbH
# All rights reserved.

"""``python -m bench.config`` — reaches :func:`bench.config.main`."""

from __future__ import annotations

import sys

from bench.config import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
