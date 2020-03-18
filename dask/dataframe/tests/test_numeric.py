import pytest
import numpy as np
import pandas as pd

from dask.array import from_array, Array
from dask.delayed import Delayed
from dask.dataframe import from_pandas, Series, to_numeric


@pytest.mark.parametrize("arg", ["5", 5, "5 "])
def test_to_numeric_on_scalars(arg):
    output = to_numeric(arg)
    assert isinstance(output, Delayed)
    assert output.compute() == 5


def test_to_numeric_on_dask_array():
    arg = from_array(["1.0", "2", -3, 5.1])
    expected = np.array([1.0, 2.0, -3.0, 5.1])
    output = to_numeric(arg)
    assert isinstance(output, Array)
    assert list(output.compute()) == list(expected)


def test_to_numeric_on_dask_dataframe_series():
    s = pd.Series(["1.0", "2", -3, -5.1])
    arg = from_pandas(s, npartitions=2)
    expected = pd.to_numeric(s)
    output = to_numeric(arg)
    assert output.dtype == "int64"
    assert isinstance(output, Series)
    assert list(output.compute()) == list(expected)


def test_to_numeric_on_dask_dataframe_series_with_meta():
    s = pd.Series(["1.0", "2", -3, -5.1])
    arg = from_pandas(s, npartitions=2)
    expected = pd.to_numeric(s)
    output = to_numeric(arg, meta=pd.Series([], dtype="float64"))
    assert output.dtype == "float64"
    assert isinstance(output, Series)
    assert list(output.compute()) == list(expected)


def test_to_numeric_on_dask_dataframe_dataframe_raises_error():
    s = pd.Series(["1.0", "2", -3, -5.1])
    df = pd.DataFrame({"a": s, "b": s})
    arg = from_pandas(df, npartitions=2)
    with pytest.raises(TypeError, match="arg must be a list, tuple, dask."):
        to_numeric(arg)
