"""A bounded search with one observation per count and no confirmation passes."""

MIN_PROGRESS = 0.99


def capacity(start, maximum, probe, *, known=None):
    """Double/halve to bracket, then narrow to 10% or one item."""
    if not 1 <= start <= maximum:
        raise ValueError("Expected 1 <= items_start <= items_max")
    tested = {count: passed for count, passed in (known or {}).items() if 1 <= count <= maximum}
    passing = [count for count, passed in tested.items() if passed]
    if passing:
        start = max(passing)
    else:
        start = min([start] + list(tested))
    failed_above = [count for count, passed in tested.items() if not passed and count >= start]
    ceiling = min([maximum] + failed_above)

    def passes(items):
        if items not in tested:
            tested[items] = bool(probe(items))
        return tested[items]

    lower, upper = 0, None
    count = start
    if passes(count):
        lower = count
        while lower < ceiling:
            count = min(ceiling, lower * 2)
            if not passes(count):
                upper = count
                break
            lower = count
    else:
        upper = count
        while count > 1:
            count = max(1, count // 2)
            if passes(count):
                lower = count
                break
            upper = count
    while lower and upper is not None and upper - lower > max(1, lower // 10):
        count = (lower + upper) // 2
        if passes(count):
            lower = count
        else:
            upper = count
    return dict(
        passing_items=lower,
        failing_items=upper,
        at_least=lower == maximum,
        tested_items=list(tested),
        minimum_progress=MIN_PROGRESS,
    )
