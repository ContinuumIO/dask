import sys

import numpy as np
import pytest
import sparse

import dask.array as da
from dask.array.utils import assert_eq


@pytest.mark.skipif(sys.version_info[:2] == (3, 4),
                    reason='indexing issues')
@pytest.mark.parametrize('func', [
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
    lambda x: x.dot(np.arange(x.shape[-1])),
    lambda x: x.dot(np.eye(x.shape[-1])),
    lambda x: da.tensordot(x, np.ones(x.shape[:2]), axes=[(0, 1), (0, 1)]),
    lambda x: x.sum(axis=0),
    lambda x: x.max(axis=0),
    lambda x: x.sum(axis=(1, 2)),
    lambda x: x.astype(np.complex128),
    lambda x: x.map_blocks(lambda x: x * 2),
    lambda x: x.round(1),
    lambda x: x.reshape((6, 4)),
    lambda x: abs(x),
    lambda x: x > 0.5,
    lambda x: x.rechunk((4, 4, 4)),
    lambda x: x.rechunk((2, 2, 1)),
])
def test_basic(func):
    x = da.random.random((2, 3, 4), chunks=(1, 2, 2))
    x[x < 0.8] = 0

    y = x.map_blocks(sparse.COO.from_numpy)

    xx = func(x)
    yy = func(y)

    assert_eq(xx, yy)

    if yy.shape:
        assert isinstance(yy.compute(), sparse.COO)


def test_tensordot():
    x = da.random.random((2, 3, 4), chunks=(1, 2, 2))
    x[x < 0.8] = 0
    y = da.random.random((4, 3, 2), chunks=(2, 2, 1))
    y[y < 0.8] = 0

    xx = x.map_blocks(sparse.COO.from_numpy)
    yy = y.map_blocks(sparse.COO.from_numpy)

    assert_eq(da.tensordot(x, y, axes=(2, 0)),
              da.tensordot(xx, yy, axes=(2, 0)))
    assert_eq(da.tensordot(x, y, axes=(1, 1)),
              da.tensordot(xx, yy, axes=(1, 1)))
    assert_eq(da.tensordot(x, y, axes=((1, 2), (1, 0))),
              da.tensordot(xx, yy, axes=((1, 2), (1, 0))))
