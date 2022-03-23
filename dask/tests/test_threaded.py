import gc
import os
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.pool import ThreadPool
from time import sleep, time

import pytest

import dask
from dask.system import CPU_COUNT
from dask.threaded import get, get_executor
from dask.utils_test import add, inc


def test_get():
    dsk = {"x": 1, "y": 2, "z": (inc, "x"), "w": (add, "z", "y")}
    assert get(dsk, "w") == 4
    assert get(dsk, ["w", "z"]) == (4, 2)


def test_nested_get():
    dsk = {"x": 1, "y": 2, "a": (add, "x", "y"), "b": (sum, ["x", "y"])}
    assert get(dsk, ["a", "b"]) == (3, 3)


def test_get_without_computation():
    dsk = {"x": 1}
    assert get(dsk, "x") == 1


def test_broken_callback():
    from dask.callbacks import Callback

    def _f_ok(*args, **kwargs):
        pass

    def _f_broken(*args, **kwargs):
        raise ValueError("my_exception")

    dsk = {"x": 1}

    with Callback(start=_f_broken, finish=_f_ok):
        with Callback(start=_f_ok, finish=_f_ok):
            with pytest.raises(ValueError, match="my_exception"):
                get(dsk, "x")


def bad(x):
    raise ValueError()


def test_exceptions_rise_to_top():
    dsk = {"x": 1, "y": (bad, "x")}
    pytest.raises(ValueError, lambda: get(dsk, "y"))


@pytest.mark.parametrize("pool_typ", [ThreadPool, ThreadPoolExecutor])
def test_reuse_pool(pool_typ):
    with pool_typ(CPU_COUNT) as pool:
        with dask.config.set(pool=pool):
            assert get({"x": (inc, 1)}, "x") == 2
            assert get({"x": (inc, 1)}, "x") == 2


@pytest.mark.parametrize("pool_typ", [ThreadPool, ThreadPoolExecutor])
def test_pool_kwarg(pool_typ):
    def f():
        sleep(0.01)
        return threading.get_ident()

    dsk = {("x", i): (f,) for i in range(30)}
    dsk["x"] = (len, (set, [("x", i) for i in range(len(dsk))]))

    with pool_typ(3) as pool:
        assert get(dsk, "x", pool=pool) == 3


@pytest.mark.parametrize("in_thread", [False, True])
def test_num_workers_equals_cpu_count_or_0_uses_default_executor(in_thread):
    """Ensure that within a thread, `None`, `0` and `CPU_COUNT` for
    `num_workers` all result in the same executor with CPU_COUNT threads. Check
    that this is true both for the main thread and in a background thread.
    """

    def test():
        default = get_executor(None)
        # reused, None means CPU_COUNT
        assert get_executor(None) is default
        # 0 means CPU_COUNT
        assert get_executor(0) is default
        # CPU_COUNT specified manually also reuses
        assert get_executor(CPU_COUNT) is default

    if in_thread:
        t = threading.Thread(target=test)
        t.start()
        t.join()
    else:
        test()


def test_executors_cleaned_up_when_background_thread_closes():
    executor = None

    def test():
        nonlocal executor
        executor = get_executor(2)

    t = threading.Thread(target=test)
    t.start()
    t.join()

    # Run a full GC cycle, just to be sure the background thread was collected
    gc.collect()

    # Unfortunately there's no public way to check if an executor is still
    # active, the best we can do is try to use it and if it errors it's shutdown.
    with pytest.raises(RuntimeError):
        executor.submit(lambda x: x + 1, 1)


def test_threaded_within_thread():
    L = []

    def f(i):
        result = get({"x": (lambda: i,)}, "x", num_workers=2)
        L.append(result)

    before = threading.active_count()

    for i in range(20):
        t = threading.Thread(target=f, args=(1,))
        t.daemon = True
        t.start()
        t.join()
        assert L == [1]
        del L[:]

    start = time()  # wait for most threads to join
    while threading.active_count() > before + 10:
        sleep(0.01)
        assert time() < start + 5


def test_dont_spawn_too_many_threads():
    before = threading.active_count()

    dsk = {("x", i): (lambda: i,) for i in range(10)}
    dsk["x"] = (sum, list(dsk))
    for i in range(20):
        get(dsk, "x", num_workers=4)

    after = threading.active_count()

    assert after <= before + 8


def test_dont_spawn_too_many_threads_CPU_COUNT():
    before = threading.active_count()

    dsk = {("x", i): (lambda: i,) for i in range(10)}
    dsk["x"] = (sum, list(dsk))
    for i in range(20):
        get(dsk, "x")

    after = threading.active_count()

    assert after <= before + CPU_COUNT * 2


def test_thread_safety():
    def f(x):
        return 1

    dsk = {"x": (sleep, 0.05), "y": (f, "x")}

    L = []

    def test_f():
        L.append(get(dsk, "y"))

    threads = []
    for i in range(20):
        t = threading.Thread(target=test_f)
        t.daemon = True
        t.start()
        threads.append(t)

    for thread in threads:
        thread.join()

    assert L == [1] * 20


@pytest.mark.slow
def test_interrupt():
    # Windows implements `queue.get` using polling,
    # which means we can set an exception to interrupt the call to `get`.
    # Python 3 on other platforms requires sending SIGINT to the main thread.
    if os.name == "nt":
        from _thread import interrupt_main
    else:
        main_thread = threading.get_ident()

        def interrupt_main() -> None:
            signal.pthread_kill(main_thread, signal.SIGINT)

    # 7 seconds is is how long the test will take when you factor in teardown.
    # Don't set it too short or the test will become flaky on non-performing CI
    dsk = {("x", i): (sleep, 7) for i in range(20)}
    dsk["x"] = (len, list(dsk.keys()))

    # 3 seconds is how long the test will take without teardown
    interrupter = threading.Timer(3, interrupt_main)
    interrupter.start()

    start = time()
    with pytest.raises(KeyboardInterrupt):
        get(dsk, "x")
    stop = time()
    assert stop < start + 6
