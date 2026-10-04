"""Bounded cron passes over work with durable per-record checkpoints."""
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass


@dataclass
class BatchResult:
    succeeded: int
    failed: int
    deferred: int


def run_checkpointed_batch(rows, process, *, workers, label, progress_every=100,
                           error_limit=100):
    """Drain active records, retry failures once, and leave unstarted rows due.

    ``process(row, retry)`` must persist its own checkpoint and return a bool.
    The soft budget reserves its final 10% for retries. The parent's longer
    hard timeout remains a fail-safe for hung requests; no futures are queued
    beyond the worker count, so shutdown only waits for active records.
    """
    seconds = max(1, float(os.getenv('ENRICHMENT_BATCH_SECONDS', '10800')))
    started = time.monotonic()
    deadline = started + seconds
    initial_deadline = started + seconds * 0.9
    succeeded = attempted = 0
    failures = []
    workers = max(1, workers)

    def outcome(future):
        try:
            return bool(future.result())
        except Exception as exc:
            print(f'{label}: record failed: {exc}', flush=True)
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        remaining = iter(rows)
        pending = {}
        while True:
            while (len(pending) < workers and len(failures) < error_limit
                   and time.monotonic() < initial_deadline):
                row = next(remaining, None)
                if row is None:
                    break
                pending[pool.submit(process, row, False)] = row
            if not pending:
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                row = pending.pop(future)
                if outcome(future):
                    succeeded += 1
                else:
                    failures.append(row)
                attempted += 1
                if attempted % progress_every == 0 or attempted == len(rows):
                    print(f'{label}: {attempted}/{len(rows)}; '
                          f'failures={len(failures)}', flush=True)

        if failures:
            print(f'{label}: retrying up to {len(failures)} failed records once', flush=True)
        retry_rows = iter(failures)
        recovered = 0
        while True:
            while len(pending) < workers and time.monotonic() < deadline:
                row = next(retry_rows, None)
                if row is None:
                    break
                pending[pool.submit(process, row, True)] = row
            if not pending:
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                recovered += outcome(future)

    result = BatchResult(succeeded + recovered, len(failures) - recovered,
                         len(rows) - attempted)
    print(f'{label}: {result.succeeded} succeeded; {result.failed} unresolved; '
          f'{result.deferred} deferred to the next run', flush=True)
    return result
