import pytest

pytest.importorskip("scipy")

import numpy as np
import scipy.sparse.linalg

import dask.array as da


def test_LinearOperator():
    X = np.random.random(size=(3, 2))
    y = np.random.random(size=(2, 1))
    w = np.random.random(size=(3, 1))
    square = np.random.random(size=(2, 2))

    dX = da.from_array(X, chunks=(2, 1))

    npLO = scipy.sparse.linalg.aslinearoperator(X)
    # TODO: stop using private method https://github.com/dask/dask/issues/8682
    daLO = scipy.sparse.linalg._interface.MatrixLinearOperator(dX)

    functions = [lambda x, y: x.matvec(y), lambda x, y: x * y, lambda x, y: x.dot(y)]
    for func in functions:
        assert np.allclose(func(npLO, y), func(daLO, y))

    assert np.allclose(npLO.matmat(square), daLO.matmat(square))

    assert np.allclose(npLO.rmatvec(w), daLO.rmatvec(w))

    assert npLO.dtype == daLO.dtype
    assert npLO.shape == daLO.shape
