"""Add selected configurations to a named counter run without replacing evidence."""

from contextlib import closing

from common.bench_db import BenchDB, benchmark_scope
from subscription.options import cases


def saved_cases(store):
    """Keep explicit batches so adding one observer does not expand older batches."""
    requested = store.stored_metadata.get("requested_cases")
    if requested is not None:
        return requested
    return cases(*store.get_config(store.suite))


def amended_plan(database, store):
    uniform, _ = store.get_config(store.suite)
    previous = saved_cases(store)
    with benchmark_scope(None), closing(BenchDB(database, store.suite)) as current:
        current_uniform, current_varying = current.get_config(store.suite)
    additions = [case for case in cases(uniform, current_varying) if case not in previous]
    maximum = max(uniform["items_max"], current_uniform["items_max"])
    if additions or maximum > uniform["items_max"]:
        if any(current_uniform[key] != value for key, value in uniform.items() if key != "items_max"):
            raise ValueError("Amend keeps items_start and min_publishes; restore those settings or use a new run")
    return {**uniform, "items_max": maximum}, previous + additions
