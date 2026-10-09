"""Bounded FIFO admission shared by synchronous callers and async transports."""

import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from functools import wraps
from threading import Event, Lock
from time import monotonic


class AdmissionError(Exception):
    status = 503
    retry_after = 1

    def __init__(self, name: str, reason: str):
        self.reason = reason
        self.message = f"{name} is busy. Retry after at least 1 second with backoff."
        super().__init__(self.message)


@dataclass(eq=False)
class _Ticket:
    deadline: float
    ready: Future = field(default_factory=Future)
    state: str = "waiting"


class BoundedAdmission:
    """Wait without occupying an executor thread; retain slots until work finishes.

    A queue is local to one Workspace/process and is not durable. Synchronous
    library callers share the same slots but wait on their own calling thread.
    """

    def __init__(self, concurrency: int, max_waiting: int, timeout: float, *, name="Memory"):
        if concurrency < 1 or max_waiting < 0 or not 0 <= timeout <= 60:
            raise ValueError("Admission needs positive concurrency and bounded queue settings")
        self.concurrency, self.max_waiting, self.timeout = concurrency, max_waiting, timeout
        self.name = name
        self._lock = Lock()
        self._waiting = deque()
        self._active = 0
        self._closed = False
        self._idle = Event()
        self._idle.set()
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix=name)

    def _reserve(self):
        with self._lock:
            if self._closed:
                raise AdmissionError(self.name, "closed")
            self._expire_waiters()
            ticket = _Ticket(monotonic() + self.timeout)
            if self._active < self.concurrency:
                self._activate(ticket)
            elif len(self._waiting) >= self.max_waiting:
                raise AdmissionError(self.name, "full")
            elif self.timeout == 0:
                raise AdmissionError(self.name, "timeout")
            else:
                self._waiting.append(ticket)
            return ticket

    def _activate(self, ticket):
        ticket.state = "active"
        self._active += 1
        self._idle.clear()
        ticket.ready.set_result(None)

    def _expire_waiters(self):
        now = monotonic()
        while self._waiting and self._waiting[0].deadline <= now:
            ticket = self._waiting.popleft()
            ticket.state = "released"
            ticket.ready.set_exception(AdmissionError(self.name, "timeout"))

    def _release(self, ticket):
        with self._lock:
            if ticket.state == "released":
                return
            if ticket.state == "waiting":
                self._waiting.remove(ticket)
                ticket.ready.cancel()
            else:
                self._active -= 1
            ticket.state = "released"
            self._expire_waiters()
            while not self._closed and self._waiting and self._active < self.concurrency:
                self._activate(self._waiting.popleft())
            if self._active == 0:
                self._idle.set()

    def run(self, function, *args, **kwargs):
        ticket = self._reserve()
        try:
            try:
                ticket.ready.result(timeout=max(0, ticket.deadline - monotonic()))
            except TimeoutError:
                raise AdmissionError(self.name, "timeout") from None
            return function(*args, **kwargs)
        finally:
            self._release(ticket)

    @staticmethod
    def _observe(future):
        # A disconnected client may stop awaiting a worker that subsequently fails.
        if not future.cancelled():
            future.exception()

    async def arun(self, function, *args, **kwargs):
        ticket = self._reserve()
        ready = asyncio.wrap_future(ticket.ready)
        ready.add_done_callback(self._observe)
        submitted = False
        try:
            if ticket.state != "active":
                try:
                    await asyncio.wait_for(
                        asyncio.shield(ready), max(0, ticket.deadline - monotonic())
                    )
                except TimeoutError:
                    raise AdmissionError(self.name, "timeout") from None
            else:
                # The concurrent Future is already ready; avoid a zero-timeout race.
                ticket.ready.result()
            context = copy_context()
            with self._lock:
                if self._closed:
                    raise AdmissionError(self.name, "closed")
                work = self._executor.submit(context.run, function, *args, **kwargs)
                submitted = True
            # Only actual worker completion releases submitted capacity. Cancelling
            # the awaiting request cannot stop a Python thread or undo its writes.
            work.add_done_callback(lambda _: self._release(ticket))
            result = asyncio.wrap_future(work)
            result.add_done_callback(self._observe)
            return await asyncio.shield(result)
        finally:
            if not submitted:
                self._release(ticket)

    async def aclose(self):
        with self._lock:
            self._closed = True
            while self._waiting:
                ticket = self._waiting.popleft()
                ticket.state = "released"
                ticket.ready.set_exception(AdmissionError(self.name, "closed"))

        def finish():
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._idle.wait()

        # Running work must finish before the composition root closes its providers.
        await asyncio.to_thread(finish)


def admitted(lane: str):
    """Keep sync Workspace APIs while allowing transport admission before dispatch."""

    def decorate(function):
        @wraps(function)
        def command(self, identity, *args, **kwargs):
            identity.check()
            return getattr(self, lane).run(function, self, identity, *args, **kwargs)

        async def asynchronous(self, identity, *args, **kwargs):
            identity.check()
            return await getattr(self, lane).arun(function, self, identity, *args, **kwargs)

        command.admitted_async = asynchronous
        return command

    return decorate


async def dispatch(function, identity, *args):
    """Capture identity before queueing; Workspace rechecks live grants after admission."""
    identity.check()
    asynchronous = getattr(function, "admitted_async", None)
    if asynchronous is not None:
        return await asynchronous(function.__self__, identity, *args)
    return await asyncio.to_thread(function, identity, *args)
