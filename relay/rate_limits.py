"""Wait for temporary API rate limits without blocking the Stop button."""
import random


class Recovery:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.reset()

    def reset(self):
        self.attempts = 0
        self.waited = 0.0

    def wait(self, failure, stopped, emit, *, partial=False):
        limit = failure.get("rate_limit")
        if not self.enabled or not limit or stopped.is_set():
            return False
        backoff = min(60, 2 ** min(self.attempts, 6))
        minimum = max(limit.get("retry_after") or 0, backoff)
        delay = minimum + random.uniform(0.05, 0.25)
        self.attempts += 1
        emit("rate_wait", seconds=delay, waited=self.waited, attempt=self.attempts, partial=partial)
        remaining = delay
        while remaining > 0:
            interval = min(remaining, 10)
            if stopped.wait(interval):
                return False
            remaining -= interval
            if remaining > 0:
                emit("ping")
        self.waited += delay
        emit("rate_resume", waited=self.waited, partial=partial)
        return True
