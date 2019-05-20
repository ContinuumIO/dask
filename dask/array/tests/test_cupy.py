import numpy as np
import pytest

import dask.array as da
from dask.array.utils import assert_eq, same_keys, AxisError, IS_NEP18_ACTIVE
from dask.array.gufunc import apply_gufunc

missing_arrfunc_cond = not IS_NEP18_ACTIVE
missing_arrfunc_reason = "NEP-18 support is not available in NumPy"

cupy = pytest.importorskip('cupy')


functions = [
    lambda x: x,
    lambda x: da.expm1(x),
    lambda x: 2 * x,
    lambda x: x / 2,
    lambda x: x**2,
    lambda x: x + x,
    lambda x: x * x,
    lambda x: x[0],
    lambda x: x[:, 1],
    lambda x: x[:1, None, 1:3],
    lambda x: x.T,
    lambda x: da.transpose(x, (1, 2, 0)),
    lambda x: x.sum(),
    pytest.param(lambda x: x.mean(),
                 marks=pytest.mark.xfail(
                 reason='requires NumPy>=1.17 and CuPy support for shape argument in *_like functions.')),
    lambda x: x.moment(order=0),
    pytest.param(lambda x: x.std(),
                 marks=pytest.mark.xfail(
                 reason='requires NumPy>=1.17 and CuPy support for shape argument in *_like functions.')),
    pytest.param(lambda x: x.var(),
                 marks=pytest.mark.xfail(
                 reason='requires NumPy>=1.17 and CuPy support for shape argument in *_like functions.')),
    pytest.param(lambda x: x.dot(np.arange(x.shape[-1])),
                 marks=pytest.mark.xfail(reason='cupy.dot(numpy) fails')),
    pytest.param(lambda x: x.dot(np.eye(x.shape[-1])),
                 marks=pytest.mark.xfail(reason='cupy.dot(numpy) fails')),
    pytest.param(lambda x: da.tensordot(x, np.ones(x.shape[:2]), axes=[(0, 1), (0, 1)]),
                 marks=pytest.mark.xfail(reason='cupy.dot(numpy) fails')),
    lambda x: x.sum(axis=0),
    lambda x: x.max(axis=0),
    lambda x: x.sum(axis=(1, 2)),
    lambda x: x.astype(np.complex128),
    lambda x: x.map_blocks(lambda x: x * 2),
    pytest.param(lambda x: x.round(1),
                 marks=pytest.mark.xfail(reason="cupy doesn't support round")),
    lambda x: x.reshape((x.shape[0] * x.shape[1], x.shape[2])),
    # Rechunking here is required, see https://github.com/dask/dask/issues/2561
    lambda x: (x.rechunk(x.shape)).reshape((x.shape[1], x.shape[0], x.shape[2])),
    lambda x: x.reshape((x.shape[0], x.shape[1], x.shape[2] / 2, x.shape[2] / 2)),
    lambda x: abs(x),
    lambda x: x > 0.5,
    lambda x: x.rechunk((4, 4, 4)),
    lambda x: x.rechunk((2, 2, 1)),
    pytest.param(lambda x: da.einsum("ijk,ijk", x, x),
                 marks=pytest.mark.xfail(
                     reason='depends on resolution of https://github.com/numpy/numpy/issues/12974'))
]


@pytest.mark.parametrize('func', functions)
def test_basic(func):
    c = cupy.random.random((2, 3, 4))
    n = c.get()
    dc = da.from_array(c, chunks=(1, 2, 2), asarray=False)
    dn = da.from_array(n, chunks=(1, 2, 2))

    ddc = func(dc)
    ddn = func(dn)

    assert_eq(ddc, ddn)

    if ddc.shape:
        result = ddc.compute(scheduler='single-threaded')
        assert isinstance(result, cupy.ndarray)


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_diag():
    v = cupy.arange(11)
    assert_eq(da.diag(v), cupy.diag(v))

    v = v + v + 3
    darr = da.diag(v)
    cupyarr = cupy.diag(v)
    assert_eq(darr, cupyarr)

    x = cupy.arange(64).reshape((8, 8))
    assert_eq(da.diag(x), cupy.diag(x))


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_diagonal():
    v = cupy.arange(11)
    with pytest.raises(ValueError):
        da.diagonal(v)

    v = cupy.arange(4).reshape((2, 2))
    with pytest.raises(ValueError):
        da.diagonal(v, axis1=0, axis2=0)

    with pytest.raises(AxisError):
        da.diagonal(v, axis1=-4)

    with pytest.raises(AxisError):
        da.diagonal(v, axis2=-4)

    v = cupy.arange(4 * 5 * 6).reshape((4, 5, 6))
    v = da.from_array(v, chunks=2, asarray=False)
    assert_eq(da.diagonal(v), np.diagonal(v))
    # Empty diagonal.
    assert_eq(da.diagonal(v, offset=10), np.diagonal(v, offset=10))
    assert_eq(da.diagonal(v, offset=-10), np.diagonal(v, offset=-10))
    assert isinstance(da.diagonal(v).compute(), cupy.core.core.ndarray)

    with pytest.raises(ValueError):
        da.diagonal(v, axis1=-2)

    # Negative axis.
    assert_eq(da.diagonal(v, axis1=-1), np.diagonal(v, axis1=-1))
    assert_eq(da.diagonal(v, offset=1, axis1=-1), np.diagonal(v, offset=1, axis1=-1))

    # Heterogenous chunks.
    v = cupy.arange(2 * 3 * 4 * 5 * 6).reshape((2, 3, 4, 5, 6))
    v = da.from_array(v, chunks=(1, (1, 2), (1, 2, 1), (2, 1, 2), (5, 1)), asarray=False)

    assert_eq(da.diagonal(v), np.diagonal(v))
    assert_eq(da.diagonal(v, offset=2, axis1=3, axis2=1),
              np.diagonal(v, offset=2, axis1=3, axis2=1))

    assert_eq(da.diagonal(v, offset=-2, axis1=3, axis2=1),
              np.diagonal(v, offset=-2, axis1=3, axis2=1))

    assert_eq(da.diagonal(v, offset=-2, axis1=3, axis2=4),
              np.diagonal(v, offset=-2, axis1=3, axis2=4))

    assert_eq(da.diagonal(v, 1), np.diagonal(v, 1))
    assert_eq(da.diagonal(v, -1), np.diagonal(v, -1))
    # Positional arguments
    assert_eq(da.diagonal(v, 1, 2, 1), np.diagonal(v, 1, 2, 1))


@pytest.mark.xfail(reason="no shape argument support *_like functions on CuPy yet")
@pytest.mark.skipif(np.__version__ < '1.17', reason='no shape argument for *_like functions')
@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_tril_triu():
    A = cupy.random.randn(20, 20)
    for chk in [5, 4]:
        dA = da.from_array(A, (chk, chk), asarray=False)

        assert np.allclose(da.triu(dA).compute(), np.triu(A))
        assert np.allclose(da.tril(dA).compute(), np.tril(A))

        for k in [-25, -20, -19, -15, -14, -9, -8, -6, -5, -1,
                  1, 4, 5, 6, 8, 10, 11, 15, 16, 19, 20, 21]:
            assert np.allclose(da.triu(dA, k).compute(), np.triu(A, k))
            assert np.allclose(da.tril(dA, k).compute(), np.tril(A, k))


@pytest.mark.xfail(reason="no shape argument support *_like functions on CuPy yet")
@pytest.mark.skipif(np.__version__ < '1.17', reason='no shape argument for *_like functions')
@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_tril_triu_non_square_arrays():
    A = cupy.random.randint(0, 11, (30, 35))
    dA = da.from_array(A, chunks=(5, 5), asarray=False)
    assert_eq(da.triu(dA), np.triu(A))
    assert_eq(da.tril(dA), np.tril(A))


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_apply_gufunc_axis():
    def mydiff(x):
        return np.diff(x)

    a = cupy.random.randn(3, 6, 4)
    da_ = da.from_array(a, chunks=2, asarray=False)

    m = np.diff(a, axis=1)
    dm = apply_gufunc(mydiff, "(i)->(i)", da_, axis=1, output_sizes={'i': 5},
                      allow_rechunk=True)
    assert_eq(m, dm)


def test_overlap_internal():
    x = cupy.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=(4, 4), asarray=False)

    g = da.overlap.overlap_internal(d, {0: 2, 1: 1})
    result = g.compute(scheduler='sync')
    assert g.chunks == ((6, 6), (5, 5))

    expected = np.array([
        [ 0,  1,  2,  3,  4,    3,  4,  5,  6,  7],
        [ 8,  9, 10, 11, 12,   11, 12, 13, 14, 15],
        [16, 17, 18, 19, 20,   19, 20, 21, 22, 23],
        [24, 25, 26, 27, 28,   27, 28, 29, 30, 31],
        [32, 33, 34, 35, 36,   35, 36, 37, 38, 39],
        [40, 41, 42, 43, 44,   43, 44, 45, 46, 47],

        [16, 17, 18, 19, 20,   19, 20, 21, 22, 23],
        [24, 25, 26, 27, 28,   27, 28, 29, 30, 31],
        [32, 33, 34, 35, 36,   35, 36, 37, 38, 39],
        [40, 41, 42, 43, 44,   43, 44, 45, 46, 47],
        [48, 49, 50, 51, 52,   51, 52, 53, 54, 55],
        [56, 57, 58, 59, 60,   59, 60, 61, 62, 63]])

    assert_eq(result, expected)
    assert same_keys(da.overlap.overlap_internal(d, {0: 2, 1: 1}), g)


def test_trim_internal():
    x = cupy.ones((40, 60))
    d = da.from_array(x, chunks=(10, 10), asarray=False)
    e = da.overlap.trim_internal(d, axes={0: 1, 1: 2})

    assert e.chunks == ((8, 8, 8, 8), (6, 6, 6, 6, 6, 6))


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_periodic():
    x = cupy.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=(4, 4), asarray=False)

    e = da.overlap.periodic(d, axis=0, depth=2)
    assert e.shape[0] == d.shape[0] + 4
    assert e.shape[1] == d.shape[1]

    assert_eq(e[1, :], d[-1, :])
    assert_eq(e[0, :], d[-2, :])


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_reflect():
    x = cupy.arange(10)
    d = da.from_array(x, chunks=(5, 5), asarray=False)

    e = da.overlap.reflect(d, axis=0, depth=2)
    expected = np.array([1, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 8])
    assert_eq(e, expected)

    e = da.overlap.reflect(d, axis=0, depth=1)
    expected = np.array([0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9])
    assert_eq(e, expected)


@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_nearest():
    x = cupy.arange(10)
    d = da.from_array(x, chunks=(5, 5), asarray=False)

    e = da.overlap.nearest(d, axis=0, depth=2)
    expected = np.array([0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9])
    assert_eq(e, expected)

    e = da.overlap.nearest(d, axis=0, depth=1)
    expected = np.array([0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9])
    assert_eq(e, expected)


@pytest.mark.xfail(reason="no shape argument support *_like functions on CuPy yet")
@pytest.mark.skipif(np.__version__ < '1.17', reason='no shape argument for *_like functions')
@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_constant():
    x = cupy.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=(4, 4), asarray=False)

    e = da.overlap.constant(d, axis=0, depth=2, value=10)
    assert e.shape[0] == d.shape[0] + 4
    assert e.shape[1] == d.shape[1]

    assert_eq(e[1, :], np.ones(8, dtype=x.dtype) * 10)
    assert_eq(e[-1, :], np.ones(8, dtype=x.dtype) * 10)


@pytest.mark.xfail(reason="no shape argument support *_like functions on CuPy yet")
@pytest.mark.skipif(np.__version__ < '1.17', reason='no shape argument for *_like functions')
@pytest.mark.skipif(missing_arrfunc_cond, reason=missing_arrfunc_reason)
def test_boundaries():
    x = cupy.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=(4, 4), asarray=False)

    e = da.overlap.boundaries(d, {0: 2, 1: 1}, {0: 0, 1: 'periodic'})

    expected = np.array(
        [[ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
         [ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
         [ 7, 0, 1, 2, 3, 4, 5, 6, 7, 0],
         [15, 8, 9,10,11,12,13,14,15, 8],
         [23,16,17,18,19,20,21,22,23,16],
         [31,24,25,26,27,28,29,30,31,24],
         [39,32,33,34,35,36,37,38,39,32],
         [47,40,41,42,43,44,45,46,47,40],
         [55,48,49,50,51,52,53,54,55,48],
         [63,56,57,58,59,60,61,62,63,56],
         [ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
         [ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]])
    assert_eq(e, expected)
