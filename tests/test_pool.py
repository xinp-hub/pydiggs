"""``pydiggs._pool``: spawned workers, hard timeouts, recycling and a JSON-only reply channel.

Jobs are stdlib callables so a spawned worker can import them without the test tree.
"""

from __future__ import annotations

import ast
import functools
import inspect
import json
import os
import subprocess
import threading
import time
from multiprocessing import reduction

import pytest

from pydiggs import _pool
from pydiggs._pool import (
    JobFailedError,
    JobTimeoutError,
    PoolClosedError,
    WorkerCrashedError,
    WorkerPool,
)

pytestmark = pytest.mark.timeout(120)


def _pids(pool):
    return {w.process.pid for w in pool._idle if w is not None}


def test_a_job_returns_its_bytes_untouched():
    with WorkerPool(1) as pool:
        assert pool.run(bytes.upper, b"diggs", timeout=60) == b"DIGGS"
        assert pool.run(bytes.upper, b"", timeout=60) == b""  # an empty result is a result


def test_the_parent_never_unpickles_what_a_worker_sends(monkeypatch):
    # Connection.recv() unpickles with ForkingPickler.loads; the parent must never call it.
    def refuse(*args, **kwargs):
        raise AssertionError("the parent unpickled data from a worker")

    monkeypatch.setattr(reduction.ForkingPickler, "loads", refuse)
    with WorkerPool(1) as pool:
        assert pool.run(bytes.upper, b"ok", timeout=60) == b"OK"
        with pytest.raises(JobFailedError):
            pool.run(bytes.fromhex, "zz", timeout=60)


def test_only_the_worker_loop_calls_recv():
    tree = ast.parse(inspect.getsource(_pool))
    owners = [
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "recv"
    ]
    assert owners == ["_worker_main"]


def test_a_timeout_kills_and_replaces_the_worker():
    with WorkerPool(1) as pool:
        pool.start()
        before = _pids(pool)
        started = time.monotonic()
        with pytest.raises(JobTimeoutError):
            pool.run(time.sleep, 60, timeout=1.0)
        assert time.monotonic() - started < 30
        after = _pids(pool)
        assert after and after.isdisjoint(before)  # recycled, not reused
        assert pool.run(bytes.upper, b"next", timeout=60) == b"NEXT"


def test_a_crash_replaces_the_worker():
    with WorkerPool(1) as pool:
        pool.start()
        before = _pids(pool)
        with pytest.raises(WorkerCrashedError):
            pool.run(os._exit, 3, timeout=60)
        assert _pids(pool).isdisjoint(before)
        assert pool.run(bytes.upper, b"x", timeout=60) == b"X"


def test_an_exception_is_plain_data_and_the_worker_is_replaced():
    with WorkerPool(1) as pool:
        pool.start()
        before = _pids(pool)
        with pytest.raises(JobFailedError) as caught:
            pool.run(bytes.fromhex, "zz", timeout=60)
        assert (caught.value.phase, caught.value.type_name) == ("job", "ValueError")
        assert "non-hexadecimal" in caught.value.message
        assert _pids(pool).isdisjoint(before)
        with pytest.raises(JobFailedError, match="must return bytes"):
            pool.run(len, b"abc", timeout=60)
        with pytest.raises(JobFailedError) as caught:
            pool.run(os.open, "/nonexistent/pydiggs", os.O_RDONLY, timeout=60)
        assert caught.value.errno == 2


def test_a_successful_worker_is_reused():
    with WorkerPool(1) as pool:
        pool.start()
        before = _pids(pool)
        pool.run(bytes.upper, b"a", timeout=60)
        pool.run(bytes.upper, b"b", timeout=60)
        assert _pids(pool) == before


def test_an_initialiser_failure_is_reported_and_the_slot_restarts():
    with WorkerPool(1, initializer=functools.partial(int, "x")) as pool:
        with pytest.raises(JobFailedError) as caught:
            pool.run(bytes.upper, b"a", timeout=60)
        assert (caught.value.phase, caught.value.type_name) == ("init", "ValueError")
        pool.start()
        deadline = time.monotonic() + 30
        while pool._idle[0] is not None and time.monotonic() < deadline:
            assert not pool.ready()  # a failed initialiser never becomes ready
            time.sleep(0.05)
        assert pool._idle[0] is None  # dropped; the next job starts a fresh worker


def test_a_worker_that_dies_while_starting_is_a_crash():
    with (
        WorkerPool(1, initializer=functools.partial(os._exit, 1)) as pool,
        pytest.raises(WorkerCrashedError),
    ):
        pool.run(bytes.upper, b"a", timeout=60)


def test_ready_turns_true_once_every_worker_has_started():
    with WorkerPool(2, initializer=functools.partial(bytes, 1)) as pool:
        assert not pool.ready()  # nothing started yet
        pool.start()
        deadline = time.monotonic() + 60
        while not pool.ready():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert pool.run(bytes.upper, b"a", timeout=60) == b"A"
    assert not pool.ready()  # closed


def test_threads_share_the_workers():
    results = []
    with WorkerPool(2) as pool:

        def call(n):
            results.append(pool.run(bytes.upper, f"t{n}".encode(), timeout=60))

        threads = [threading.Thread(target=call, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sorted(results) == sorted(f"T{n}".encode() for n in range(6))


def test_close_is_idempotent_and_refuses_new_jobs():
    pool = WorkerPool(1)
    pool.start()
    pool.close()
    pool.close()
    with pytest.raises(PoolClosedError):
        pool.run(bytes.upper, b"a", timeout=60)
    with pytest.raises(PoolClosedError):
        pool.start()


def test_close_stops_a_running_job():
    pool = WorkerPool(1)
    errors = []

    def call():
        try:
            pool.run(time.sleep, 60, timeout=None)
        except Exception as exc:  # recorded for the assertion below
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 30
    while not pool._busy or not next(iter(pool._busy)).ready:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    time.sleep(0.2)  # let the job start sleeping
    pool.close()
    thread.join(30)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], WorkerCrashedError)


def test_a_waiting_caller_is_released_by_close():
    pool = WorkerPool(1)
    pool._idle.clear()  # every worker busy
    errors = []
    thread = threading.Thread(target=lambda: errors.append(_capture(pool)))
    thread.start()
    time.sleep(0.2)
    pool.close()
    thread.join(10)
    assert errors and isinstance(errors[0], PoolClosedError)


def _capture(pool):
    try:
        pool.run(bytes.upper, b"a", timeout=1)
    except Exception as exc:  # returned for the assertion
        return exc
    return None


def test_a_spawn_failure_keeps_the_slot(monkeypatch):
    pool = WorkerPool(1)

    def broken():
        raise OSError("no more processes")

    monkeypatch.setattr(pool, "_spawn", broken)
    with pytest.raises(OSError, match="no more processes"):
        pool.run(bytes.upper, b"a", timeout=1)
    assert pool._idle == [None]
    pool.close()


def test_a_respawn_failure_leaves_an_empty_slot(monkeypatch):
    with WorkerPool(1) as pool:
        with pytest.raises(JobFailedError):
            monkeypatch.setattr(
                pool, "_spawn", functools.partial(_spawn_once, pool, pool._spawn, [False])
            )
            pool.run(bytes.fromhex, "zz", timeout=60)
        assert pool._idle == [None]


def _spawn_once(pool, real, used):
    if used[0]:
        raise OSError("no more processes")
    used[0] = True
    return real()


def test_a_reply_over_the_limit_is_a_crash():
    with WorkerPool(1, max_reply_bytes=8) as pool, pytest.raises(WorkerCrashedError):
        pool.run(bytes.upper, b"x" * 64, timeout=60)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workers": 0},
        {"workers": True},
        {"memory_mb": 0},
        {"cpu_seconds": 0},
    ],
)
def test_bad_settings_are_refused(kwargs):
    with pytest.raises(ValueError):
        WorkerPool(**kwargs)


# --- frames and limits, in-process --------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [b"not json", b"[]", json.dumps({"phase": 1, "type": "X", "message": "", "errno": None})],
)
def test_malformed_error_frames_are_crashes(payload):
    data = payload if isinstance(payload, bytes) else payload.encode()
    with pytest.raises(WorkerCrashedError):
        raise _pool._parse_error(data)


def test_error_frames_carry_only_plain_data():
    frame = _pool._error_frame("job", FileNotFoundError(2, "gone", "/secret/path"))
    assert frame[:1] == b"E"
    failed = _pool._parse_error(frame[1:])
    assert (failed.phase, failed.type_name, failed.errno) == ("job", "FileNotFoundError", 2)
    weird = _pool._parse_error(_pool._error_frame("job", OSError("x"))[1:])
    assert weird.errno is None
    assert str(JobFailedError("job", "KeyError", "", None)) == "KeyError"


class _Conn:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []

    def recv(self):
        if not self.incoming:
            raise EOFError
        return self.incoming.pop(0)

    def send_bytes(self, data):
        self.sent.append(data)


def test_the_worker_loop_in_process(monkeypatch):
    disabled = []
    monkeypatch.setattr(_pool, "_no_core_files", lambda: disabled.append(True))  # not pytest's
    requests = _Conn([(bytes.upper, (b"a",)), (len, (b"ab",))])
    replies = _Conn()
    _pool._worker_main(requests, replies, None, None, None)
    assert replies.sent[0] == b"I"
    assert replies.sent[1] == b"RA"
    assert replies.sent[2][:1] == b"E" and b"must return bytes" in replies.sent[2]
    assert disabled == [True]  # before the initialiser and any job
    replies = _Conn()
    _pool._worker_main(_Conn([None]), replies, None, None, None)
    assert replies.sent == [b"I"]
    replies = _Conn()
    _pool._worker_main(_Conn(), replies, None, None, None)  # EOF: the parent went away
    assert replies.sent == [b"I"]
    replies = _Conn()
    _pool._worker_main(_Conn(), replies, functools.partial(int, "x"), None, None)
    assert replies.sent[0][:1] == b"E" and b'"init"' in replies.sent[0]


def test_limits_use_setrlimit(monkeypatch):
    resource = pytest.importorskip("resource")  # POSIX only
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, value: calls.append((which, value)))
    monkeypatch.setattr(_pool, "_ENFORCES_RLIMIT_AS", True)
    _pool._limit_memory(512)
    assert calls == [(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))]
    calls.clear()
    _pool._limit_memory(None)
    monkeypatch.setattr(_pool, "_ENFORCES_RLIMIT_AS", False)
    _pool._limit_memory(512)
    assert calls == []
    monkeypatch.setattr(_pool, "_HAS_RLIMIT_CPU", True)
    monkeypatch.setattr(resource, "getrlimit", lambda which: (resource.RLIM_INFINITY, 100))
    _pool._limit_cpu(10_000)
    ((which, (soft, hard)),) = calls
    assert which == resource.RLIMIT_CPU and (soft, hard) == (100, 100)  # capped at the hard
    calls.clear()
    monkeypatch.setattr(
        resource, "getrlimit", lambda which: (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
    )
    _pool._limit_cpu(5)
    ((_, (soft, hard)),) = calls
    assert soft >= 5 and hard == resource.RLIM_INFINITY
    calls.clear()
    _pool._limit_cpu(None)
    monkeypatch.setattr(_pool, "_HAS_RLIMIT_CPU", False)
    _pool._limit_cpu(5)
    _pool._no_core_files()
    assert calls == []
    monkeypatch.setattr(_pool, "_HAS_RLIMIT_CPU", True)
    _pool._no_core_files()
    assert calls == [(resource.RLIMIT_CORE, (0, 0))]


@pytest.mark.skipif(not _pool._HAS_RLIMIT_CPU, reason="no resource module on Windows")
def test_a_real_worker_writes_no_core_file():
    resource = pytest.importorskip("resource")
    before = resource.getrlimit(resource.RLIMIT_CORE)
    with WorkerPool(1) as pool:  # a shell started by the worker inherits its limits
        limit = pool.run(subprocess.check_output, ["sh", "-c", "ulimit -c"], timeout=60)
    assert limit.strip() == b"0"
    assert resource.getrlimit(resource.RLIMIT_CORE) == before  # the parent is untouched


@pytest.mark.skipif(not _pool._HAS_RLIMIT_CPU, reason="no RLIMIT_CPU on Windows")
def test_the_cpu_backstop_stops_a_runaway_job():
    # A busy loop the parent does not time out: the kernel's CPU limit stops the worker.
    with WorkerPool(1, cpu_seconds=1) as pool, pytest.raises(WorkerCrashedError):
        pool.run(functools.reduce, pow, range(2, 10**9), timeout=None)
