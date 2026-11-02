"""Experiment-only bounded scheduling for independent candidate detail tasks.

Apply retiming, duplicate checks and scene-wide consistency in source order,
outside this mapper. No API client is created here.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def bounded_ordered_map(function, items, *, workers=2, on_completed=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    items = list(items)
    results = [None] * len(items)
    cursor, pending = 0, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while cursor < len(items) or pending:
            while cursor < len(items) and len(pending) < workers:
                pending[pool.submit(function, items[cursor])] = cursor
                cursor += 1
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                index = pending.pop(future)
                results[index] = future.result()
                if on_completed:
                    on_completed(index,results[index])
    return results
