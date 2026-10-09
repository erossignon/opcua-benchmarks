"""Runtime and evidence limits for an adaptive server-capacity estimate."""

from common.suites import ConfigOption, one_of, uniform, varying, whole_number

DEFAULT_IMPLEMENTATIONS = ("open62541", "o6-python", "asyncua", "ua-dotnet", "node-opcua")
# The optional server-only SDKs (common/sdk_workers.py) are opt-in.
IMPLEMENTATIONS = DEFAULT_IMPLEMENTATIONS + ("milo", "s2opc", "gopcua", "node-opcua-fronts")

OPTIONS = {
    "implementation": ConfigOption(
        "varying",
        list(DEFAULT_IMPLEMENTATIONS),
        varying(one_of(*IMPLEMENTATIONS)),
        "Servers to search using the same native scalar Read client. milo, s2opc and gopcua are opt-in.",
    ),
    "probe_ms": ConfigOption("uniform", 1000, uniform(whole_number(250)), "Short discovery window in milliseconds."),
    "confirm_ms": ConfigOption(
        "uniform", 2000, uniform(whole_number(1000)), "Window for each fresh repeat of a selected candidate."
    ),
    "warmup_ms": ConfigOption("uniform", 500, uniform(whole_number(100)), "Uncounted warmup before each probe."),
    "timeout_ms": ConfigOption("uniform", 2000, uniform(whole_number(1)), "Per-request response timeout."),
    "grace_ms": ConfigOption("uniform", 2500, uniform(whole_number(1)), "Maximum drain duration; at least timeout_ms."),
    "start_outstanding": ConfigOption("uniform", 8, uniform(whole_number(1)), "Initial pipeline depth for one client."),
    "max_outstanding": ConfigOption("uniform", 512, uniform(whole_number(1)), "Pipeline-depth search cap."),
    "max_clients": ConfigOption(
        "uniform", 32, uniform(whole_number(1)), "Client-process search cap; actual CPU headroom is checked."
    ),
    "max_probes": ConfigOption("uniform", 40, uniform(whole_number(3)), "Maximum new observations per server per invocation."),
    "budget_seconds": ConfigOption(
        "uniform",
        90,
        uniform(whole_number(5)),
        "Wall-clock budget per server, including startup; teardown may add up to 10 seconds.",
    ),
    "plateau_percent": ConfigOption(
        "uniform", 5, uniform(one_of(3, 5, 10)), "Legacy plateau tolerance; unused by the ranked candidate search."
    ),
}
