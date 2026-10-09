"""Admission behavior under contention, cancellation, failure, and shutdown.

The burst is a synthetic concurrency regression, not a production throughput benchmark.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from space.admission import AdmissionError, BoundedAdmission


async def wait_started(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 3), timeout=4)


async def enqueue(gate, function, *args, **kwargs):
    attempted = asyncio.Event()

    async def call():
        attempted.set()
        return await gate.arun(function, *args, **kwargs)

    task = asyncio.create_task(call())
    await attempted.wait()
    return task


def assert_admission_error(error, reason):
    assert error.value.reason == reason
    assert error.value.status == 503
    assert error.value.retry_after == 1
    assert error.value.message


def test_sync_and_async_callers_share_the_running_bound():
    async def exercise():
        gate = BoundedAdmission(2, 4, 3)
        release = threading.Event()
        started = [threading.Event() for _ in range(3)]
        lock = threading.Lock()
        running = peak = 0

        def work(index, *, suffix):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            started[index].set()
            try:
                assert release.wait(5)
                return f"{index}:{suffix}"
            finally:
                with lock:
                    running -= 1

        with ThreadPoolExecutor(max_workers=1) as callers:
            sync = callers.submit(gate.run, work, 0, suffix="sync")
            pending = []
            try:
                await wait_started(started[0])
                pending.append(await enqueue(gate, work, 1, suffix="async"))
                await wait_started(started[1])
                pending.append(await enqueue(gate, work, 2, suffix="queued"))
                assert not started[2].is_set()
                release.set()
                assert await asyncio.wait_for(asyncio.gather(*pending), 4) == [
                    "1:async",
                    "2:queued",
                ]
                assert sync.result(timeout=4) == "0:sync"
                assert peak == 2
                assert running == 0
            finally:
                release.set()
                await asyncio.gather(*pending, return_exceptions=True)
                await gate.aclose()

    asyncio.run(exercise())


def test_pending_async_calls_are_admitted_in_fifo_order():
    async def exercise():
        gate = BoundedAdmission(1, 8, 3)
        release = threading.Event()
        started = threading.Event()
        order = []

        def work(index):
            order.append(index)
            if index == 0:
                started.set()
                assert release.wait(5)
            return index

        tasks = [asyncio.create_task(gate.arun(work, 0))]
        try:
            await wait_started(started)
            for index in range(1, 9):
                tasks.append(await enqueue(gate, work, index))
            assert order == [0]
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 4) == list(range(9))
            assert order == list(range(9))
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_full_queue_rejects_sync_and_async_calls_without_running_them():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3, name="recall")
        release = threading.Event()
        started = threading.Event()
        rejected_ran = threading.Event()

        def first():
            started.set()
            assert release.wait(5)

        tasks = [asyncio.create_task(gate.arun(first))]
        try:
            await wait_started(started)
            tasks.append(await enqueue(gate, lambda: "queued"))
            with pytest.raises(AdmissionError) as error:
                await asyncio.wait_for(gate.arun(rejected_ran.set), 2)
            assert_admission_error(error, "full")
            with pytest.raises(AdmissionError) as error:
                await asyncio.wait_for(asyncio.to_thread(gate.run, rejected_ran.set), 2)
            assert_admission_error(error, "full")
            assert not rejected_ran.is_set()
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 4) == [None, "queued"]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("synchronous", [False, True])
def test_pending_deadline_expires_without_running_work_and_frees_queue_space(synchronous):
    async def exercise():
        gate = BoundedAdmission(1, 1, 0.05)
        release = threading.Event()
        started = threading.Event()
        expired_ran = threading.Event()

        def first():
            started.set()
            assert release.wait(5)

        tasks = [asyncio.create_task(gate.arun(first))]
        try:
            await wait_started(started)
            with pytest.raises(AdmissionError) as error:
                waiting = (
                    asyncio.to_thread(gate.run, expired_ran.set)
                    if synchronous
                    else gate.arun(expired_ran.set)
                )
                await asyncio.wait_for(waiting, 2)
            assert_admission_error(error, "timeout")
            assert not expired_ran.is_set()
            tasks.append(await enqueue(gate, lambda: "replacement"))
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 4) == [None, "replacement"]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_cancelling_queued_work_frees_its_place_and_never_runs_it():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3)
        release = threading.Event()
        started = threading.Event()
        cancelled_ran = threading.Event()

        def first():
            started.set()
            assert release.wait(5)

        tasks = [asyncio.create_task(gate.arun(first))]
        try:
            await wait_started(started)
            cancelled = await enqueue(gate, cancelled_ran.set)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            tasks.append(await enqueue(gate, lambda: "replacement"))
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 4) == [None, "replacement"]
            assert not cancelled_ran.is_set()
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_cancelling_running_request_keeps_capacity_until_worker_finishes():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3)
        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()
        second_started = threading.Event()

        def first():
            started.set()
            try:
                assert release.wait(5)
            finally:
                finished.set()

        first_task = asyncio.create_task(gate.arun(first))
        pending = []
        try:
            await wait_started(started)
            first_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task
            assert not finished.is_set()
            pending.append(await enqueue(gate, second_started.set))
            with pytest.raises(AdmissionError) as error:
                await asyncio.wait_for(gate.arun(lambda: None), 2)
            assert_admission_error(error, "full")
            assert not second_started.is_set()
            release.set()
            await asyncio.wait_for(asyncio.gather(*pending), 4)
            assert finished.is_set()
            assert second_started.is_set()
        finally:
            release.set()
            await asyncio.gather(first_task, *pending, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_cancelling_at_admission_handoff_never_runs_cancelled_work_or_loses_capacity():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3)
        try:
            for _ in range(20):
                release = threading.Event()
                started = threading.Event()
                cancelled_ran = threading.Event()

                def first(started=started, release=release):
                    started.set()
                    assert release.wait(5)

                active = asyncio.create_task(gate.arun(first))
                queued = None
                try:
                    await wait_started(started)
                    queued = await enqueue(gate, cancelled_ran.set)
                    # Completion may promote the ticket on the worker thread,
                    # but the queued coroutine cannot resume before cancellation.
                    release.set()
                    queued.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await queued
                    await asyncio.wait_for(active, 4)
                    assert await asyncio.wait_for(gate.arun(lambda: "next"), 2) == "next"
                    assert not cancelled_ran.is_set()
                finally:
                    release.set()
                    tasks = [task for task in (active, queued) if task is not None]
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await gate.aclose()

    asyncio.run(exercise())


def test_worker_exceptions_release_capacity_and_preserve_the_exception():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3)
        release = threading.Event()
        started = threading.Event()

        def fail():
            raise ValueError("synthetic failure")

        def delayed_failure():
            started.set()
            assert release.wait(5)
            fail()

        failed = asyncio.create_task(gate.arun(delayed_failure))
        pending = None
        try:
            await wait_started(started)
            pending = await enqueue(gate, lambda: "queued behind failure")
            release.set()
            with pytest.raises(ValueError, match="synthetic failure"):
                await asyncio.wait_for(failed, 4)
            assert await asyncio.wait_for(pending, 4) == "queued behind failure"
            with pytest.raises(ValueError, match="synthetic failure"):
                await asyncio.to_thread(gate.run, fail)
            assert await gate.arun(lambda: "after sync error") == "after sync error"
        finally:
            release.set()
            tasks = [task for task in (failed, pending) if task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_shutdown_rejects_pending_and_new_calls_and_waits_for_actual_workers():
    async def exercise():
        gate = BoundedAdmission(1, 1, 3)
        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()
        pending_ran = threading.Event()

        def first():
            started.set()
            try:
                assert release.wait(5)
                return "finished"
            finally:
                finished.set()

        first_task = asyncio.create_task(gate.arun(first))
        closing = pending = None
        try:
            await wait_started(started)
            pending = await enqueue(gate, pending_ran.set)
            closing = asyncio.create_task(gate.aclose())
            with pytest.raises(AdmissionError) as error:
                await asyncio.wait_for(pending, 2)
            assert_admission_error(error, "closed")
            assert not closing.done()
            assert not finished.is_set()
            with pytest.raises(AdmissionError) as error:
                await gate.arun(lambda: None)
            assert_admission_error(error, "closed")
            with pytest.raises(AdmissionError) as error:
                await asyncio.to_thread(gate.run, lambda: None)
            assert_admission_error(error, "closed")
            release.set()
            assert await asyncio.wait_for(first_task, 4) == "finished"
            await asyncio.wait_for(closing, 4)
            assert finished.is_set()
            assert not pending_ran.is_set()
            await gate.aclose()
        finally:
            release.set()
            tasks = [task for task in (first_task, pending, closing) if task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())


def test_shutdown_waits_for_synchronous_run_callers():
    async def exercise():
        gate = BoundedAdmission(1, 0, 3)
        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()
        closing_started = asyncio.Event()

        def work():
            started.set()
            try:
                assert release.wait(5)
                return "sync finished"
            finally:
                finished.set()

        async def close():
            closing_started.set()
            await gate.aclose()

        with ThreadPoolExecutor(max_workers=1) as callers:
            active = callers.submit(gate.run, work)
            closing = None
            try:
                await wait_started(started)
                closing = asyncio.create_task(close())
                await closing_started.wait()
                assert not closing.done()
                assert not finished.is_set()
                with pytest.raises(AdmissionError) as error:
                    await gate.arun(lambda: None)
                assert_admission_error(error, "closed")
                release.set()
                assert await asyncio.wait_for(asyncio.wrap_future(active), 4) == "sync finished"
                await asyncio.wait_for(closing, 4)
                assert finished.is_set()
            finally:
                release.set()
                if closing is not None:
                    await closing
                await gate.aclose()

    asyncio.run(exercise())


def test_busy_gate_does_not_starve_default_executor_or_an_independent_gate():
    async def exercise():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        gate = BoundedAdmission(1, 8, 3)
        independent = BoundedAdmission(1, 1, 3)
        release = threading.Event()
        started = threading.Event()

        def first():
            started.set()
            assert release.wait(5)

        tasks = [asyncio.create_task(gate.arun(first))]
        try:
            await wait_started(started)
            for _ in range(8):
                tasks.append(await enqueue(gate, lambda: "queued"))
            assert await asyncio.wait_for(asyncio.to_thread(lambda: "default"), 2) == "default"
            assert await asyncio.wait_for(independent.arun(lambda: "independent"), 2) == (
                "independent"
            )
            assert not release.is_set()
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()
            await independent.aclose()

    asyncio.run(exercise())


def test_synthetic_hundred_request_burst_completes_within_running_bound():
    async def exercise():
        concurrency = 4
        gate = BoundedAdmission(concurrency, 100, 10)
        release = threading.Event()
        saturated = threading.Event()
        lock = threading.Lock()
        running = peak = 0

        def work(index):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
                if running == concurrency:
                    saturated.set()
            try:
                assert release.wait(5)
                return index
            finally:
                with lock:
                    running -= 1

        tasks = [asyncio.create_task(gate.arun(work, index)) for index in range(100)]
        try:
            await wait_started(saturated)
            release.set()
            assert await asyncio.wait_for(asyncio.gather(*tasks), 10) == list(range(100))
            assert peak == concurrency
            assert running == 0
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gate.aclose()

    asyncio.run(exercise())
