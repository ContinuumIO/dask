from __future__ import annotations

import copy
import string
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, cast

import numpy as np
import pandas as pd

from dask.dataframe.core import tokenize
from dask.dataframe.io.io import from_map
from dask.dataframe.io.utils import DataFrameIOFunction
from dask.utils import random_state_data

__all__ = ["make_timeseries", "with_spec", "ColumnSpec", "IndexSpec", "DatasetSpec"]

default_int_args: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "poisson": ((), {"lam": 1000}),
    "normal": ((), {"scale": 1000}),
    "uniform": ((), {"high": 1000}),
    "binomial": ((1000, 0.5), {}),
}


@dataclass
class ColumnSpec:
    """Encapsulates properties of a family of columns with the same dtype.
    Different method can be specified for integer dtype ("poisson", "uniform",
    "binomial", etc.)"""

    prefix: str | None = None
    dtype: str | type | None = None
    number: int = 1
    nunique: int | None = None  # number of unique categories
    choices: list = field(default_factory=list)
    low: int | None = None
    high: int | None = None
    length: int | None = None
    random: bool = False
    method: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexSpec:
    """Properties of the dataframe index"""

    dtype: str | type = int
    start: str | None = None  # should be set for DatetimeIndex
    freq: int | str = 1  # int for RangeIndex, str for DatetimeIndex ("1H", "1D", etc.)
    partition_freq: str | None = None  # should be set for datetime index


@dataclass
class DatasetSpec:
    """Defines a dataset with random data, such as which columns and data types to generate"""

    npartitions: int = 1
    nrecords: int = 1000  # total records
    index_spec: IndexSpec = field(default_factory=IndexSpec)
    column_specs: list[ColumnSpec] = field(default_factory=list)


def make_float(n, rstate, random=False, dtype=None, **kwargs):
    if random:
        data = rstate.random(size=n, **kwargs)
        if dtype:
            data = data.astype(dtype)
        return data
    return rstate.rand(n) * 2 - 1


def make_int(
    n: int,
    rstate: Any,
    random: bool = False,
    dtype: str | type = int,
    method: str | Callable = "poisson",
    **kwargs,
):
    if random:
        data = rstate.randint(size=n, **kwargs)
    else:
        if isinstance(method, str):
            # "poisson", "binomial", etc.
            handler_args, handler_kwargs = default_int_args.get(method, ((), {}))
            handler_kwargs = copy.copy(handler_kwargs)
            handler_kwargs.update(**kwargs)
            handler = getattr(rstate, method)
            data = handler(*handler_args, size=n, **handler_kwargs)
        else:
            # method is a Callable
            data = method(state=rstate, size=n, **kwargs)
    if dtype is not None:
        data = data.astype(dtype)
    return data


names = [
    "Alice",
    "Bob",
    "Charlie",
    "Dan",
    "Edith",
    "Frank",
    "George",
    "Hannah",
    "Ingrid",
    "Jerry",
    "Kevin",
    "Laura",
    "Michael",
    "Norbert",
    "Oliver",
    "Patricia",
    "Quinn",
    "Ray",
    "Sarah",
    "Tim",
    "Ursula",
    "Victor",
    "Wendy",
    "Xavier",
    "Yvonne",
    "Zelda",
]


def make_random_string(n, rstate, length: int = 25) -> list[str]:
    choices = list(string.ascii_letters + string.digits + string.punctuation + " ")
    return ["".join(rstate.choice(choices, size=length)) for _ in range(n)]


def make_string(n, rstate, choices=None, random=False, length=None, **_):
    if random:
        return make_random_string(n, rstate, length=length)
    choices = choices or names
    return rstate.choice(choices, size=n)


def make_categorical(n, rstate, choices=None, nunique=None, **_):
    if nunique is not None:
        cat_len = len(str(nunique))
        choices = [str(x + 1).zfill(cat_len) for x in range(nunique)]
    else:
        choices = choices or names
    return pd.Categorical.from_codes(rstate.randint(0, len(choices), size=n), choices)


make: dict[type | str, Callable] = {
    float: make_float,
    int: make_int,
    str: make_string,
    object: make_string,
    "category": make_categorical,
    "int8": make_int,
    "int16": make_int,
    "int32": make_int,
    "int64": make_int,
    "float8": make_float,
    "float16": make_float,
    "float32": make_float,
    "float64": make_float,
}


class MakeDataframePart(DataFrameIOFunction):
    """
    Wrapper Class for ``make_dataframe_part``
    Makes a timeseries partition.
    """

    def __init__(self, index_dtype, dtypes, kwargs, columns=None):
        self.index_dtype = index_dtype
        self._columns = columns or list(dtypes.keys())
        self.dtypes = dtypes
        self.kwargs = kwargs

    @property
    def columns(self):
        return self._columns

    def project_columns(self, columns):
        """Return a new MakeTimeseriesPart object with
        a sub-column projection.
        """
        if columns == self.columns:
            return self
        return MakeDataframePart(
            self.index_dtype,
            self.dtypes,
            self.kwargs,
            columns=columns,
        )

    def __call__(self, part):
        divisions, state_data = part
        if isinstance(state_data, int):
            state_data = random_state_data(1, state_data)
        return make_dataframe_part(
            self.index_dtype,
            divisions[0],
            divisions[1],
            self.dtypes,
            self.columns,
            state_data,
            self.kwargs,
        )


def make_dataframe_part(index_dtype, start, end, dtypes, columns, state_data, kwargs):
    state = np.random.RandomState(state_data)
    if pd.api.types.is_datetime64_any_dtype(index_dtype):
        index = pd.date_range(
            start=start, end=end, freq=kwargs.get("freq"), name="timestamp"
        )
    elif pd.api.types.is_integer_dtype(index_dtype):
        step = kwargs.get("freq")
        index = pd.RangeIndex(start=start, stop=end + step, step=step).astype(
            index_dtype
        )
    else:
        raise TypeError(f"Unhandled index dtype: {index_dtype}")
    df = make_partition(columns, dtypes, index, kwargs, state)
    while df.index[-1] >= end:
        df = df.iloc[:-1]
    return df


def make_partition(columns: list, dtypes: dict[str, type | str], index, kwargs, state):
    data = {}
    for k, dt in dtypes.items():
        kws = {
            kk.rsplit("_", 1)[1]: v
            for kk, v in kwargs.items()
            if kk.rsplit("_", 1)[0] == k
        }
        # Note: we compute data for all dtypes in order, not just those in the output
        # columns. This ensures the same output given the same state_data, regardless
        # of whether there is any column projection.
        # cf. https://github.com/dask/dask/pull/9538#issuecomment-1267461887
        result = make[dt](len(index), state, **kws)
        if k in columns:
            data[k] = result
    return pd.DataFrame(data, index=index, columns=columns)


def make_timeseries(
    start="2000-01-01",
    end="2000-12-31",
    dtypes=None,
    freq="10s",
    partition_freq="1M",
    seed=None,
    **kwargs,
):
    """Create timeseries dataframe with random data

    Parameters
    ----------
    start: datetime (or datetime-like string)
        Start of time series
    end: datetime (or datetime-like string)
        End of time series
    dtypes: dict (optional)
        Mapping of column names to types.
        Valid types include {float, int, str, 'category'}
    freq: string
        String like '2s' or '1H' or '12W' for the time series frequency
    partition_freq: string
        String like '1M' or '2Y' to divide the dataframe into partitions
    seed: int (optional)
        Randomstate seed
    kwargs:
        Keywords to pass down to individual column creation functions.
        Keywords should be prefixed by the column name and then an underscore.

    Examples
    --------
    >>> import dask.dataframe as dd
    >>> df = dd.demo.make_timeseries('2000', '2010',
    ...                              {'value': float, 'name': str, 'id': int},
    ...                              freq='2H', partition_freq='1D', seed=1)
    >>> df.head()  # doctest: +SKIP
                           id      name     value
    2000-01-01 00:00:00   969     Jerry -0.309014
    2000-01-01 02:00:00  1010       Ray -0.760675
    2000-01-01 04:00:00  1016  Patricia -0.063261
    2000-01-01 06:00:00   960   Charlie  0.788245
    2000-01-01 08:00:00  1031     Kevin  0.466002
    """
    if dtypes is None:
        dtypes = {"name": str, "id": int, "x": float, "y": float}

    divisions = list(pd.date_range(start=start, end=end, freq=partition_freq))
    npartitions = len(divisions) - 1
    if seed is None:
        # Get random integer seed for each partition. We can
        # call `random_state_data` in `MakeDataframePart`
        state_data = np.random.randint(2e9, size=npartitions)
    else:
        state_data = random_state_data(npartitions, seed)

    # Build parts
    parts = []
    for i in range(len(divisions) - 1):
        parts.append((divisions[i : i + 2], state_data[i]))

    kwargs["freq"] = freq
    index_dtype = "datetime64[ns]"
    meta_start, meta_end = list(pd.date_range(start="2000", freq=freq, periods=2))

    # Construct the output collection with from_map
    return from_map(
        MakeDataframePart(index_dtype, dtypes, kwargs),
        parts,
        meta=make_dataframe_part(
            index_dtype,
            meta_start,
            meta_end,
            dtypes,
            list(dtypes.keys()),
            state_data[0],
            kwargs,
        ),
        divisions=divisions,
        label="make-timeseries",
        token=tokenize(start, end, dtypes, freq, partition_freq, state_data),
        enforce_metadata=False,
    )


def with_spec(spec: DatasetSpec, seed: int | None = None):
    """Generate a random dataset according to provided spec

    Parameters
    ----------
    spec : DatasetSpec
        Specify all the parameters of the dataset
    seed: int (optional)
        Randomstate seed

    Examples
    --------
    >>> from dask.dataframe.io.demo import ColumnSpec, DatasetSpec, with_spec

    >>> ddf = with_spec(
    ...        DatasetSpec(
    ...             npartitions=10,
    ...             nrecords=10_000,
    ...             column_specs=[
    ...                 ColumnSpec(dtype=int, number=2, prefix="p"),
    ...                 ColumnSpec(dtype=int, number=2, prefix="n", method="normal"),
    ...                 ColumnSpec(dtype=float, number=2, prefix="f"),
    ...                 ColumnSpec(dtype=str, prefix="s", number=2, random=True, length=10),
    ...                 ColumnSpec(dtype="category", prefix="c", choices=["Y", "N"]),
    ...             ],
    ...        ),
    ...        seed=42)
    >>> ddf.head(10)  # doctest: +SKIP

         p1    p2    n1    n2        f1        f2          s1          s2 c1
    0  1002   972  -811    20  0.640846 -0.176875  L#h98#}J`?  _8C607/:6e  N
    1   985   982 -1663  -777  0.790257  0.792796  u:XI3,omoZ  w~@ /d)'-@  N
    2   947   970   799  -269  0.740869 -0.118413  O$dnwCuq\\  !WtSe+(;#9  Y
    3  1003   983  1133   521 -0.987459  0.278154  j+Qr_2{XG&  &XV7cy$y1T  Y
    4  1017  1049   826     5 -0.875667 -0.744359  \4bJ3E-{:o  {+jC).?vK+  Y
    5   984  1017  -492  -399  0.748181  0.293761  ~zUNHNgD"!  yuEkXeVot|  Y
    6   992  1027  -856    67 -0.125132 -0.234529  j.7z;o]Gc9  g|Fi5*}Y92  Y
    7  1011   974   762 -1223  0.471696  0.937935  yT?j~N/-u]  JhEB[W-}^$  N
    8   984   974   856    74  0.109963  0.367864  _j"&@ i&;/  OYXQ)w{hoH  N
    9  1030  1001  -792  -262  0.435587 -0.647970  Pmrwl{{|.K  3UTqM$86Sg  N
    """
    if len(spec.column_specs) == 0:
        spec.column_specs = [
            ColumnSpec(prefix="i", dtype=int, low=0, high=1_000_000, random=True),
            ColumnSpec(prefix="f", dtype=float, random=True),
            ColumnSpec(prefix="c", dtype="category", choices=["a", "b", "c", "d"]),
            ColumnSpec(prefix="s", dtype=str),
        ]

    columns = []
    dtypes = {}
    partition_freq: str | int
    if pd.api.types.is_datetime64_any_dtype(spec.index_spec.dtype):
        assert spec.index_spec.partition_freq is not None
        assert spec.index_spec.start is not None
        start = pd.Timestamp(spec.index_spec.start)
        step = spec.index_spec.freq
        partition_freq = spec.index_spec.partition_freq
        end = pd.Timestamp(spec.index_spec.start) + spec.nrecords * pd.Timedelta(step)
        divisions = list(pd.date_range(start=start, end=end, freq=partition_freq))
        if divisions[-1] < end:
            divisions.append(end)
        meta_start, meta_end = start, start + pd.Timedelta(step)
    elif pd.api.types.is_integer_dtype(spec.index_spec.dtype):
        step = int(spec.index_spec.freq)
        partition_freq = spec.nrecords * step // spec.npartitions
        end = spec.nrecords * step - 1
        divisions = list(pd.RangeIndex(0, stop=end, step=partition_freq))
        if divisions[-1] < (end + 1):
            divisions.append(end + 1)
        meta_start, meta_end = 0, step
    else:
        raise ValueError(f"Unhandled index dtype: {spec.index_spec.dtype}")

    kwargs: dict[str, Any] = {"freq": step}
    for col in spec.column_specs:
        if col.prefix:
            prefix = col.prefix
        elif isinstance(col.dtype, str):
            prefix = col.dtype
        elif hasattr(col.dtype, "name"):
            prefix = col.dtype.name  # type: ignore
        else:
            prefix = col.dtype.__name__  # type: ignore
        for i in range(col.number):
            col_n = i + 1
            while (col_name := f"{prefix}{col_n}") in dtypes:
                col_n = col_n + 1
            columns.append(col_name)
            dtypes[col_name] = col.dtype
            kwargs.update(
                {
                    f"{col_name}_{k}": v
                    for k, v in asdict(col).items()
                    if k not in {"prefix", "number", "kwargs"} and v not in (None, [])
                }
            )
            # set untyped kwargs, if any
            for kw_name, kw_val in col.kwargs.items():
                kwargs[f"{col_name}_{kw_name}"] = kw_val

    npartitions = len(divisions) - 1
    if seed is None:
        state_data = cast(list[Any], np.random.randint(int(2e9), size=npartitions))
    else:
        state_data = random_state_data(npartitions, seed)

    parts = [(divisions[i : i + 2], state_data[i]) for i in range(npartitions)]

    return from_map(
        MakeDataframePart(spec.index_spec.dtype, dtypes, kwargs, columns=columns),
        parts,
        meta=make_dataframe_part(
            spec.index_spec.dtype,
            meta_start,
            meta_end,
            dtypes,
            columns,
            state_data[0],
            kwargs,
        ),
        divisions=divisions,
        label="make-random",
        token=tokenize(0, spec.nrecords, dtypes, step, partition_freq, state_data),
        enforce_metadata=False,
    )
