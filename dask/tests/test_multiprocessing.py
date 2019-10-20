import sys
import multiprocessing
from operator import add
import pickle
import random

import numpy as np

import pytest
import dask
from dask import compute, delayed
from dask.multiprocessing import get, _dumps, get_context, remote_exception
from dask.utils_test import inc


def unrelated_function_global(a):
    return np.array([a])


def my_small_function_global(a, b):
    return a + b


def test_pickle_globals():
    """ Unrelated globals should not be included in serialized bytes """
    b = _dumps(my_small_function_global)
    assert b"my_small_function_global" in b
    assert b"unrelated_function_global" not in b
    assert b"numpy" not in b


def test_pickle_locals():
    """Unrelated locals should not be included in serialized bytes
    """
    pytest.importorskip("cloudpickle")

    def unrelated_function_local(a):
        return np.array([a])

    def my_small_function_local(a, b):
        return a + b

    b = _dumps(my_small_function_local)
    assert b"my_small_function_global" not in b
    assert b"my_small_function_local" in b
    assert b"unrelated_function_local" not in b


def bad():
    raise ValueError("12345")


def test_errors_propagate():
    dsk = {"x": (bad,)}

    try:
        get(dsk, "x")
    except Exception as e:
        assert isinstance(e, ValueError)
        assert "12345" in str(e)


def test_remote_exception():
    e = TypeError("hello")
    a = remote_exception(e, "traceback-body")
    b = remote_exception(e, "traceback-body")

    assert type(a) == type(b)
    assert isinstance(a, TypeError)
    assert "hello" in str(a)
    assert "Traceback" in str(a)
    assert "traceback-body" in str(a)


def make_bad_result():
    return lambda x: x + 1


def test_unpicklable_results_generate_errors():

    dsk = {"x": (make_bad_result,)}

    try:
        get(dsk, "x")
    except Exception as e:
        # can't use type because pickle / cPickle distinction
        assert type(e).__name__ in ("PicklingError", "AttributeError")


class NotUnpickleable(object):
    def __getstate__(self):
        return ()

    def __setstate__(self, state):
        raise ValueError("Can't unpickle me")


def test_unpicklable_args_generate_errors():
    a = NotUnpickleable()

    dsk = {"x": (bool, a)}

    with pytest.raises(ValueError):
        get(dsk, "x")

    dsk = {"x": (bool, "a"), "a": a}

    with pytest.raises(ValueError):
        get(dsk, "x")


def test_reuse_pool():
    with multiprocessing.Pool() as pool:
        with dask.config.set(pool=pool):
            assert get({"x": (inc, 1)}, "x") == 2
            assert get({"x": (inc, 1)}, "x") == 2


def test_dumps_loads():
    with dask.config.set(func_dumps=pickle.dumps, func_loads=pickle.loads):
        assert get({"x": 1, "y": (add, "x", 2)}, "y") == 3


def test_fuse_doesnt_clobber_intermediates():
    d = {"x": 1, "y": (inc, "x"), "z": (add, 10, "y")}
    assert get(d, ["y", "z"]) == (2, 12)


def test_optimize_graph_false():
    from dask.callbacks import Callback

    d = {"x": 1, "y": (inc, "x"), "z": (add, 10, "y")}
    keys = []
    with Callback(pretask=lambda key, *args: keys.append(key)):
        get(d, "z", optimize_graph=False)
    assert len(keys) == 2


# Don't apply the @delayed decorator here or it
# will break when cloudpickle isn't installed
def random_tuple():
    return tuple(random.randint(0, 10000) for i in range(5))


@pytest.mark.parametrize("random", [np.random, random])
def test_random_seeds(random):
    N = 10
    f = delayed(random_tuple, pure=False)
    with dask.config.set(scheduler="processes"):
        (results,) = compute([f() for _ in range(N)])

    assert len(set(results)) == N


def check_for_pytest():
    """We check for spawn by ensuring subprocess doesn't have modules only
    parent process should have:
    """
    import sys

    return "FAKE_MODULE_FOR_TEST" in sys.modules


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows doesn't support different contexts"
)
def test_custom_context_used_python3_posix():
    """ The 'multiprocessing.context' config is used to create the pool.

    We assume default is 'fork', and therefore test for 'spawn'.  If default
    context is changed this test will need to be modified to be different than
    that.
    """
    sys.modules["FAKE_MODULE_FOR_TEST"] = 1
    try:
        with dask.config.set({"multiprocessing.context": "spawn"}):
            result = get({"x": (check_for_pytest,)}, "x")
        assert not result
    finally:
        del sys.modules["FAKE_MODULE_FOR_TEST"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows doesn't support different contexts"
)
def test_get_context_using_python3_posix():
    """ get_context() respects configuration.

    If default context is changed this test will need to change too.
    """
    assert get_context() is multiprocessing.get_context(None)
    with dask.config.set({"multiprocessing.context": "forkserver"}):
        assert get_context() is multiprocessing.get_context("forkserver")
    with dask.config.set({"multiprocessing.context": "spawn"}):
        assert get_context() is multiprocessing.get_context("spawn")


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX supports different contexts")
def test_custom_context_ignored_elsewhere():
    """ On Windows, setting 'multiprocessing.context' doesn't explode.

    Presumption is it's not used since unsupported, but mostly we care about
    not breaking anything.
    """
    assert get({"x": (inc, 1)}, "x") == 2
    with pytest.warns(UserWarning):
        with dask.config.set({"multiprocessing.context": "forkserver"}):
            assert get({"x": (inc, 1)}, "x") == 2


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX supports different contexts")
def test_get_context_always_default():
    """ On Python 2/Windows, get_context() always returns same context."""
    assert get_context() is multiprocessing
    with pytest.warns(UserWarning):
        with dask.config.set({"multiprocessing.context": "forkserver"}):
            assert get_context() is multiprocessing
