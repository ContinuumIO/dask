import sys
from copy import copy
from operator import getitem, add

import pandas as pd
import pandas.util.testing as tm
import numpy as np
import pytest

import dask
from dask.async import get_sync
from dask import delayed
from dask.utils import ignoring, put_lines
import dask.dataframe as dd

from dask.dataframe.core import (repartition_divisions, aca, _concat,
                                 _Frame, Scalar)
from dask.dataframe.methods import boundary_slice
from dask.dataframe.utils import assert_eq, make_meta, assert_max_deps


dsk = {('x', 0): pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]},
                              index=[0, 1, 3]),
       ('x', 1): pd.DataFrame({'a': [4, 5, 6], 'b': [3, 2, 1]},
                              index=[5, 6, 8]),
       ('x', 2): pd.DataFrame({'a': [7, 8, 9], 'b': [0, 0, 0]},
                              index=[9, 9, 9])}
meta = make_meta({'a': 'i8', 'b': 'i8'}, index=pd.Index([], 'i8'))
d = dd.DataFrame(dsk, 'x', meta, [0, 5, 9, 9])
full = d.compute()


def test_Dataframe():
    expected = pd.Series([2, 3, 4, 5, 6, 7, 8, 9, 10],
                         index=[0, 1, 3, 5, 6, 8, 9, 9, 9],
                         name='a')

    assert_eq(d['a'] + 1, expected)

    tm.assert_index_equal(d.columns, pd.Index(['a', 'b']))

    assert_eq(d[d['b'] > 2], full[full['b'] > 2])
    assert_eq(d[['a', 'b']], full[['a', 'b']])
    assert_eq(d.a, full.a)
    assert d.b.mean().compute() == full.b.mean()
    assert np.allclose(d.b.var().compute(), full.b.var())
    assert np.allclose(d.b.std().compute(), full.b.std())

    assert d.index._name == d.index._name  # this is deterministic

    assert repr(d)


def test_head_tail():
    assert_eq(d.head(2), full.head(2))
    assert_eq(d.head(3), full.head(3))
    assert_eq(d.head(2), dsk[('x', 0)].head(2))
    assert_eq(d['a'].head(2), full['a'].head(2))
    assert_eq(d['a'].head(3), full['a'].head(3))
    assert_eq(d['a'].head(2), dsk[('x', 0)]['a'].head(2))
    assert (sorted(d.head(2, compute=False).dask) ==
            sorted(d.head(2, compute=False).dask))
    assert (sorted(d.head(2, compute=False).dask) !=
            sorted(d.head(3, compute=False).dask))

    assert_eq(d.tail(2), full.tail(2))
    assert_eq(d.tail(3), full.tail(3))
    assert_eq(d.tail(2), dsk[('x', 2)].tail(2))
    assert_eq(d['a'].tail(2), full['a'].tail(2))
    assert_eq(d['a'].tail(3), full['a'].tail(3))
    assert_eq(d['a'].tail(2), dsk[('x', 2)]['a'].tail(2))
    assert (sorted(d.tail(2, compute=False).dask) ==
            sorted(d.tail(2, compute=False).dask))
    assert (sorted(d.tail(2, compute=False).dask) !=
            sorted(d.tail(3, compute=False).dask))


def test_head_npartitions():
    assert_eq(d.head(5, npartitions=2), full.head(5))
    assert_eq(d.head(5, npartitions=2, compute=False), full.head(5))
    assert_eq(d.head(5, npartitions=-1), full.head(5))
    assert_eq(d.head(7, npartitions=-1), full.head(7))
    assert_eq(d.head(2, npartitions=-1), full.head(2))
    with pytest.raises(ValueError):
        d.head(2, npartitions=5)


@pytest.mark.skipif(sys.version_info[:2] == (3, 3),
                    reason="Python3.3 uses pytest2.7.2, w/o warns method")
def test_head_npartitions_warn():
    with pytest.warns(None):
        d.head(100)

    with pytest.warns(None):
        d.head(7)

    with pytest.warns(None):
        d.head(7, npartitions=2)


def test_index_head():
    assert_eq(d.index.head(2), full.index[:2])
    assert_eq(d.index.head(3), full.index[:3])


def test_Series():
    assert isinstance(d.a, dd.Series)
    assert isinstance(d.a + 1, dd.Series)
    assert_eq((d + 1), full + 1)
    assert repr(d.a).startswith('dd.Series')


def test_repr():
    df = pd.DataFrame({'x': list(range(100))})
    ddf = dd.from_pandas(df, 3)

    for x in [ddf, ddf.index, ddf.x]:
        assert type(x).__name__ in repr(x)
        assert x._name[:5] in repr(x)
        assert str(x.npartitions) in repr(x)
        assert len(repr(x)) < 80


def test_Index():
    for case in [pd.DataFrame(np.random.randn(10, 5), index=list('abcdefghij')),
                 pd.DataFrame(np.random.randn(10, 5),
                              index=pd.date_range('2011-01-01', freq='D',
                                                  periods=10))]:
        ddf = dd.from_pandas(case, 3)
        assert_eq(ddf.index, case.index)
        assert repr(ddf.index).startswith('dd.Index')
        pytest.raises(AttributeError, lambda: ddf.index.index)


def test_Scalar():
    val = np.int64(1)
    s = Scalar({('a', 0): val}, 'a', 'i8')
    assert hasattr(s, 'dtype')
    assert 'dtype' in dir(s)
    assert_eq(s, val)
    assert repr(s) == "dd.Scalar<a, dtype=int64>"

    val = pd.Timestamp('2001-01-01')
    s = Scalar({('a', 0): val}, 'a', val)
    assert not hasattr(s, 'dtype')
    assert 'dtype' not in dir(s)
    assert_eq(s, val)
    assert repr(s) == "dd.Scalar<a, type=Timestamp>"


def test_attributes():
    assert 'a' in dir(d)
    assert 'foo' not in dir(d)
    pytest.raises(AttributeError, lambda: d.foo)

    df = dd.from_pandas(pd.DataFrame({'a b c': [1, 2, 3]}), npartitions=2)
    assert 'a b c' not in dir(df)
    df = dd.from_pandas(pd.DataFrame({'a': [1, 2], 5: [1, 2]}), npartitions=2)
    assert 'a' in dir(df)
    assert 5 not in dir(df)

    df = dd.from_pandas(tm.makeTimeDataFrame(), npartitions=3)
    pytest.raises(AttributeError, lambda: df.foo)


def test_column_names():
    tm.assert_index_equal(d.columns, pd.Index(['a', 'b']))
    tm.assert_index_equal(d[['b', 'a']].columns, pd.Index(['b', 'a']))
    assert d['a'].name == 'a'
    assert (d['a'] + 1).name == 'a'
    assert (d['a'] + d['b']).name is None


def test_index_names():
    assert d.index.name is None

    idx = pd.Index([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], name='x')
    df = pd.DataFrame(np.random.randn(10, 5), idx)
    ddf = dd.from_pandas(df, 3)
    assert ddf.index.name == 'x'
    assert ddf.index.compute().name == 'x'


def test_set_index():
    dsk = {('x', 0): pd.DataFrame({'a': [1, 2, 3], 'b': [4, 2, 6]},
                                  index=[0, 1, 3]),
           ('x', 1): pd.DataFrame({'a': [4, 5, 6], 'b': [3, 5, 8]},
                                  index=[5, 6, 8]),
           ('x', 2): pd.DataFrame({'a': [7, 8, 9], 'b': [9, 1, 8]},
                                  index=[9, 9, 9])}
    d = dd.DataFrame(dsk, 'x', meta, [0, 4, 9, 9])
    full = d.compute()

    d2 = d.set_index('b', npartitions=3)
    assert d2.npartitions == 3
    assert d2.index.name == 'b'
    assert_eq(d2, full.set_index('b'))

    d3 = d.set_index(d.b, npartitions=3)
    assert d3.npartitions == 3
    assert d3.index.name == 'b'
    assert_eq(d3, full.set_index(full.b))

    d4 = d.set_index('b')
    assert d4.index.name == 'b'
    assert_eq(d4, full.set_index('b'))


def test_set_index_interpolate():
    df = pd.DataFrame({'x': [1, 1, 1, 3, 3], 'y': [1., 1, 1, 1, 2]})
    d = dd.from_pandas(df, 2)

    d1 = d.set_index('x', npartitions=3)
    assert d1.npartitions == 3
    assert set(d1.divisions) == set([1, 2, 3])

    d2 = d.set_index('y', npartitions=3)
    assert d2.divisions[0] == 1.
    assert 1. < d2.divisions[1] < d2.divisions[2] < 2.
    assert d2.divisions[3] == 2.


def test_set_index_interpolate_int():
    L = sorted(list(range(0, 200, 10)) * 2)
    df = pd.DataFrame({'x': 2 * L})
    d = dd.from_pandas(df, 2)
    d1 = d.set_index('x', npartitions=10)
    assert all(np.issubdtype(type(x), np.integer) for x in d1.divisions)


def test_set_index_timezone():
    s_naive = pd.Series(pd.date_range('20130101', periods=3))
    s_aware = pd.Series(pd.date_range('20130101', periods=3, tz='US/Eastern'))
    df = pd.DataFrame({'tz': s_aware, 'notz': s_naive})
    d = dd.from_pandas(df, 2)

    d1 = d.set_index('notz', npartitions=2)
    s1 = pd.DatetimeIndex(s_naive.values, dtype=s_naive.dtype)
    assert d1.divisions[0] == s_naive[0] == s1[0]
    assert d1.divisions[2] == s_naive[2] == s1[2]

    # We currently lose "freq".  Converting data with pandas-defined dtypes
    # to numpy or pure Python can be lossy like this.
    d2 = d.set_index('tz', npartitions=2)
    s2 = pd.DatetimeIndex(s_aware.values, dtype=s_aware.dtype)
    assert d2.divisions[0] == s2[0]
    assert d2.divisions[2] == s2[2]
    assert d2.divisions[0].tz == s2[0].tz
    assert d2.divisions[0].tz is not None
    s2badtype = pd.DatetimeIndex(s_aware.values, dtype=s_naive.dtype)
    with pytest.raises(TypeError):
        d2.divisions[0] == s2badtype[0]


@pytest.mark.parametrize('drop', [True, False])
def test_set_index_drop(drop):

    pdf = pd.DataFrame({'A': list('ABAABBABAA'),
                        'B': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        'C': [1, 2, 3, 2, 1, 3, 2, 4, 2, 3]})
    ddf = dd.from_pandas(pdf, 3)

    assert_eq(ddf.set_index('A', drop=drop),
              pdf.set_index('A', drop=drop))
    assert_eq(ddf.set_index('B', drop=drop),
              pdf.set_index('B', drop=drop))
    assert_eq(ddf.set_index('C', drop=drop),
              pdf.set_index('C', drop=drop))
    assert_eq(ddf.set_index(ddf.A, drop=drop),
              pdf.set_index(pdf.A, drop=drop))
    assert_eq(ddf.set_index(ddf.B, drop=drop),
              pdf.set_index(pdf.B, drop=drop))
    assert_eq(ddf.set_index(ddf.C, drop=drop),
              pdf.set_index(pdf.C, drop=drop))

    # numeric columns
    pdf = pd.DataFrame({0: list('ABAABBABAA'),
                        1: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        2: [1, 2, 3, 2, 1, 3, 2, 4, 2, 3]})
    ddf = dd.from_pandas(pdf, 3)
    assert_eq(ddf.set_index(0, drop=drop),
              pdf.set_index(0, drop=drop))
    assert_eq(ddf.set_index(2, drop=drop),
              pdf.set_index(2, drop=drop))


def test_set_index_raises_error_on_bad_input():
    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7],
                       'b': [7, 6, 5, 4, 3, 2, 1]})
    ddf = dd.from_pandas(df, 2)

    msg = r"Dask dataframe does not yet support multi-indexes"
    with tm.assertRaisesRegexp(NotImplementedError, msg):
        ddf.set_index(['a', 'b'])


def test_rename_columns():
    # GH 819
    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7],
                       'b': [7, 6, 5, 4, 3, 2, 1]})
    ddf = dd.from_pandas(df, 2)

    ddf.columns = ['x', 'y']
    df.columns = ['x', 'y']
    tm.assert_index_equal(ddf.columns, pd.Index(['x', 'y']))
    tm.assert_index_equal(ddf._meta.columns, pd.Index(['x', 'y']))
    assert_eq(ddf, df)

    msg = r"Length mismatch: Expected axis has 2 elements, new values have 4 elements"
    with tm.assertRaisesRegexp(ValueError, msg):
        ddf.columns = [1, 2, 3, 4]

    # Multi-index columns
    df = pd.DataFrame({('A', '0') : [1, 2, 2, 3], ('B', 1) : [1, 2, 3, 4]})
    ddf = dd.from_pandas(df, npartitions=2)

    df.columns = ['x', 'y']
    ddf.columns = ['x', 'y']
    tm.assert_index_equal(ddf.columns, pd.Index(['x', 'y']))
    tm.assert_index_equal(ddf._meta.columns, pd.Index(['x', 'y']))
    assert_eq(ddf, df)


def test_rename_series():
    # GH 819
    s = pd.Series([1, 2, 3, 4, 5, 6, 7], name='x')
    ds = dd.from_pandas(s, 2)

    s.name = 'renamed'
    ds.name = 'renamed'
    assert s.name == 'renamed'
    assert_eq(ds, s)

    ind = s.index
    dind = ds.index
    ind.name = 'renamed'
    dind.name = 'renamed'
    assert ind.name == 'renamed'
    assert_eq(dind, ind)


def test_describe():
    # prepare test case which approx quantiles will be the same as actuals
    s = pd.Series(list(range(20)) * 4)
    df = pd.DataFrame({'a': list(range(20)) * 4, 'b': list(range(4)) * 20})

    ds = dd.from_pandas(s, 4)
    ddf = dd.from_pandas(df, 4)

    assert_eq(s.describe(), ds.describe())
    assert_eq(df.describe(), ddf.describe())
    assert_eq(s.describe(), ds.describe(split_every=2))
    assert_eq(df.describe(), ddf.describe(split_every=2))

    assert ds.describe(split_every=2)._name != ds.describe()._name
    assert ddf.describe(split_every=2)._name != ddf.describe()._name

    # remove string columns
    df = pd.DataFrame({'a': list(range(20)) * 4, 'b': list(range(4)) * 20,
                       'c': list('abcd') * 20})
    ddf = dd.from_pandas(df, 4)
    assert_eq(df.describe(), ddf.describe())
    assert_eq(df.describe(), ddf.describe(split_every=2))


def test_cumulative():
    df = pd.DataFrame(np.random.randn(100, 5), columns=list('abcde'))
    ddf = dd.from_pandas(df, 5)

    assert_eq(ddf.cumsum(), df.cumsum())
    assert_eq(ddf.cumprod(), df.cumprod())
    assert_eq(ddf.cummin(), df.cummin())
    assert_eq(ddf.cummax(), df.cummax())

    assert_eq(ddf.cumsum(axis=1), df.cumsum(axis=1))
    assert_eq(ddf.cumprod(axis=1), df.cumprod(axis=1))
    assert_eq(ddf.cummin(axis=1), df.cummin(axis=1))
    assert_eq(ddf.cummax(axis=1), df.cummax(axis=1))

    assert_eq(ddf.a.cumsum(), df.a.cumsum())
    assert_eq(ddf.a.cumprod(), df.a.cumprod())
    assert_eq(ddf.a.cummin(), df.a.cummin())
    assert_eq(ddf.a.cummax(), df.a.cummax())

    # With NaNs
    df = pd.DataFrame({'a': [1, 2, np.nan, 4, 5, 6, 7, 8],
                       'b': [1, 2, np.nan, np.nan, np.nan, 5, np.nan, np.nan],
                       'c': [np.nan] * 8})
    ddf = dd.from_pandas(df, 3)

    assert_eq(df.cumsum(), ddf.cumsum())
    assert_eq(df.cummin(), ddf.cummin())
    assert_eq(df.cummax(), ddf.cummax())
    assert_eq(df.cumprod(), ddf.cumprod())

    assert_eq(df.cumsum(skipna=False), ddf.cumsum(skipna=False))
    assert_eq(df.cummin(skipna=False), ddf.cummin(skipna=False))
    assert_eq(df.cummax(skipna=False), ddf.cummax(skipna=False))
    assert_eq(df.cumprod(skipna=False), ddf.cumprod(skipna=False))

    assert_eq(df.cumsum(axis=1), ddf.cumsum(axis=1))
    assert_eq(df.cummin(axis=1), ddf.cummin(axis=1))
    assert_eq(df.cummax(axis=1), ddf.cummax(axis=1))
    assert_eq(df.cumprod(axis=1), ddf.cumprod(axis=1))

    assert_eq(df.cumsum(axis=1, skipna=False), ddf.cumsum(axis=1, skipna=False))
    assert_eq(df.cummin(axis=1, skipna=False), ddf.cummin(axis=1, skipna=False))
    assert_eq(df.cummax(axis=1, skipna=False), ddf.cummax(axis=1, skipna=False))
    assert_eq(df.cumprod(axis=1, skipna=False), ddf.cumprod(axis=1, skipna=False))


def test_dropna():
    df = pd.DataFrame({'x': [np.nan, 2, 3, 4, np.nan, 6],
                       'y': [1, 2, np.nan, 4, np.nan, np.nan],
                       'z': [1, 2, 3, 4, np.nan, np.nan]},
                      index=[10, 20, 30, 40, 50, 60])
    ddf = dd.from_pandas(df, 3)

    assert_eq(ddf.x.dropna(), df.x.dropna())
    assert_eq(ddf.y.dropna(), df.y.dropna())
    assert_eq(ddf.z.dropna(), df.z.dropna())

    assert_eq(ddf.dropna(), df.dropna())
    assert_eq(ddf.dropna(how='all'), df.dropna(how='all'))
    assert_eq(ddf.dropna(subset=['x']), df.dropna(subset=['x']))
    assert_eq(ddf.dropna(subset=['y', 'z']), df.dropna(subset=['y', 'z']))
    assert_eq(ddf.dropna(subset=['y', 'z'], how='all'),
              df.dropna(subset=['y', 'z'], how='all'))


@pytest.mark.parametrize('lower, upper', [(2, 5), (2.5, 3.5)])
def test_clip(lower, upper):

    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7, 8, 9],
                       'b': [3, 5, 2, 5, 7, 2, 4, 2, 4]})
    ddf = dd.from_pandas(df, 3)

    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9])
    ds = dd.from_pandas(s, 3)

    assert_eq(ddf.clip(lower=lower, upper=upper),
              df.clip(lower=lower, upper=upper))
    assert_eq(ddf.clip(lower=lower), df.clip(lower=lower))
    assert_eq(ddf.clip(upper=upper), df.clip(upper=upper))

    assert_eq(ds.clip(lower=lower, upper=upper),
              s.clip(lower=lower, upper=upper))
    assert_eq(ds.clip(lower=lower), s.clip(lower=lower))
    assert_eq(ds.clip(upper=upper), s.clip(upper=upper))

    assert_eq(ddf.clip_lower(lower), df.clip_lower(lower))
    assert_eq(ddf.clip_lower(upper), df.clip_lower(upper))
    assert_eq(ddf.clip_upper(lower), df.clip_upper(lower))
    assert_eq(ddf.clip_upper(upper), df.clip_upper(upper))

    assert_eq(ds.clip_lower(lower), s.clip_lower(lower))
    assert_eq(ds.clip_lower(upper), s.clip_lower(upper))
    assert_eq(ds.clip_upper(lower), s.clip_upper(lower))
    assert_eq(ds.clip_upper(upper), s.clip_upper(upper))


def test_where_mask():
    pdf1 = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7, 8, 9],
                         'b': [3, 5, 2, 5, 7, 2, 4, 2, 4]})
    ddf1 = dd.from_pandas(pdf1, 2)
    pdf2 = pd.DataFrame({'a': [True, False, True] * 3,
                         'b': [False, False, True] * 3})
    ddf2 = dd.from_pandas(pdf2, 2)

    # different index
    pdf3 = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7, 8, 9],
                         'b': [3, 5, 2, 5, 7, 2, 4, 2, 4]},
                        index=[0, 1, 2, 3, 4, 5, 6, 7, 8])
    ddf3 = dd.from_pandas(pdf3, 2)
    pdf4 = pd.DataFrame({'a': [True, False, True] * 3,
                         'b': [False, False, True] * 3},
                        index=[5, 6, 7, 8, 9, 10, 11, 12, 13])
    ddf4 = dd.from_pandas(pdf4, 2)

    # different columns
    pdf5 = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6, 7, 8, 9],
                         'b': [9, 4, 2, 6, 2, 3, 1, 6, 2],
                         'c': [5, 6, 7, 8, 9, 10, 11, 12, 13]},
                        index=[0, 1, 2, 3, 4, 5, 6, 7, 8])
    ddf5 = dd.from_pandas(pdf5, 2)
    pdf6 = pd.DataFrame({'a': [True, False, True] * 3,
                         'b': [False, False, True] * 3,
                         'd': [False] * 9,
                         'e': [True] * 9},
                        index=[5, 6, 7, 8, 9, 10, 11, 12, 13])
    ddf6 = dd.from_pandas(pdf6, 2)

    cases = [(ddf1, ddf2, pdf1, pdf2),
             (ddf1.repartition([0, 3, 6, 8]), ddf2, pdf1, pdf2),
             (ddf1, ddf4, pdf3, pdf4),
             (ddf3.repartition([0, 4, 6, 8]), ddf4.repartition([5, 9, 10, 13]),
              pdf3, pdf4),
             (ddf5, ddf6, pdf5, pdf6),
             (ddf5.repartition([0, 4, 7, 8]), ddf6, pdf5, pdf6),

             # use pd.DataFrame as cond
             (ddf1, pdf2, pdf1, pdf2),
             (ddf1, pdf4, pdf3, pdf4),
             (ddf5, pdf6, pdf5, pdf6)]

    for ddf, ddcond, pdf, pdcond in cases:
        assert isinstance(ddf, dd.DataFrame)
        assert isinstance(ddcond, (dd.DataFrame, pd.DataFrame))
        assert isinstance(pdf, pd.DataFrame)
        assert isinstance(pdcond, pd.DataFrame)

        assert_eq(ddf.where(ddcond), pdf.where(pdcond))
        assert_eq(ddf.mask(ddcond), pdf.mask(pdcond))
        assert_eq(ddf.where(ddcond, -ddf), pdf.where(pdcond, -pdf))
        assert_eq(ddf.mask(ddcond, -ddf), pdf.mask(pdcond, -pdf))

        # ToDo: Should work on pandas 0.17
        # https://github.com/pydata/pandas/pull/10283
        # assert_eq(ddf.where(ddcond.a, -ddf), pdf.where(pdcond.a, -pdf))
        # assert_eq(ddf.mask(ddcond.a, -ddf), pdf.mask(pdcond.a, -pdf))

        assert_eq(ddf.a.where(ddcond.a), pdf.a.where(pdcond.a))
        assert_eq(ddf.a.mask(ddcond.a), pdf.a.mask(pdcond.a))
        assert_eq(ddf.a.where(ddcond.a, -ddf.a), pdf.a.where(pdcond.a, -pdf.a))
        assert_eq(ddf.a.mask(ddcond.a, -ddf.a), pdf.a.mask(pdcond.a, -pdf.a))


def test_map_partitions_multi_argument():
    assert_eq(dd.map_partitions(lambda a, b: a + b, d.a, d.b),
              full.a + full.b)
    assert_eq(dd.map_partitions(lambda a, b, c: a + b + c, d.a, d.b, 1),
              full.a + full.b + 1)


def test_map_partitions():
    assert_eq(d.map_partitions(lambda df: df, meta=d), full)
    assert_eq(d.map_partitions(lambda df: df), full)
    result = d.map_partitions(lambda df: df.sum(axis=1))
    assert_eq(result, full.sum(axis=1))

    # dtype is inferred to np.array default (so on windows this is int32)
    assert_eq(d.map_partitions(lambda df: 1), pd.Series([1, 1, 1], dtype=np.int),
              check_divisions=False)
    x = Scalar({('x', 0): 1}, 'x', int)
    result = dd.map_partitions(lambda x: 2, x)
    assert result.dtype in (np.int32, np.int64) and result.compute() == 2
    result = dd.map_partitions(lambda x: 4.0, x)
    assert result.dtype == np.float64 and result.compute() == 4.0


def test_map_partitions_names():
    func = lambda x: x
    assert (sorted(dd.map_partitions(func, d, meta=d).dask) ==
            sorted(dd.map_partitions(func, d, meta=d).dask))
    assert (sorted(dd.map_partitions(lambda x: x, d, meta=d, token=1).dask) ==
            sorted(dd.map_partitions(lambda x: x, d, meta=d, token=1).dask))

    func = lambda x, y: x
    assert (sorted(dd.map_partitions(func, d, d, meta=d).dask) ==
            sorted(dd.map_partitions(func, d, d, meta=d).dask))


def test_map_partitions_column_info():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    a = dd.from_pandas(df, npartitions=2)

    b = dd.map_partitions(lambda x: x, a, meta=a)
    tm.assert_index_equal(b.columns, a.columns)
    assert_eq(df, b)

    b = dd.map_partitions(lambda x: x, a.x, meta=a.x)
    assert b.name == a.x.name
    assert_eq(df.x, b)

    b = dd.map_partitions(lambda x: x, a.x, meta=a.x)
    assert b.name == a.x.name
    assert_eq(df.x, b)

    b = dd.map_partitions(lambda df: df.x + df.y, a)
    assert isinstance(b, dd.Series)
    assert b.dtype == 'i8'

    b = dd.map_partitions(lambda df: df.x + 1, a, meta=('x', 'i8'))
    assert isinstance(b, dd.Series)
    assert b.name == 'x'
    assert b.dtype == 'i8'


def test_map_partitions_method_names():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    a = dd.from_pandas(df, npartitions=2)

    b = a.map_partitions(lambda x: x)
    assert isinstance(b, dd.DataFrame)
    tm.assert_index_equal(b.columns, a.columns)

    b = a.map_partitions(lambda df: df.x + 1)
    assert isinstance(b, dd.Series)
    assert b.dtype == 'i8'

    b = a.map_partitions(lambda df: df.x + 1, meta=('x', 'i8'))
    assert isinstance(b, dd.Series)
    assert b.name == 'x'
    assert b.dtype == 'i8'


def test_map_partitions_keeps_kwargs_in_dict():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    a = dd.from_pandas(df, npartitions=2)

    def f(s, x=1):
        return s + x

    b = a.x.map_partitions(f, x=5)

    assert "'x': 5" in str(b.dask)
    assert_eq(df.x + 5, b)

    assert a.x.map_partitions(f, x=5)._name != a.x.map_partitions(f, x=6)._name


def test_drop_duplicates():
    res = d.drop_duplicates()
    res2 = d.drop_duplicates(split_every=2)
    sol = full.drop_duplicates()
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert res._name != res2._name

    res = d.a.drop_duplicates()
    res2 = d.a.drop_duplicates(split_every=2)
    sol = full.a.drop_duplicates()
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert res._name != res2._name

    res = d.index.drop_duplicates()
    res2 = d.index.drop_duplicates(split_every=2)
    sol = full.index.drop_duplicates()
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert res._name != res2._name


def test_drop_duplicates_subset():
    df = pd.DataFrame({'x': [1, 2, 3, 1, 2, 3],
                       'y': ['a', 'a', 'b', 'b', 'c', 'c']})
    ddf = dd.from_pandas(df, npartitions=2)

    for kwarg in [{'keep': 'first'}, {'keep': 'last'}]:
        assert_eq(df.x.drop_duplicates(**kwarg),
                  ddf.x.drop_duplicates(**kwarg))
        for ss in [['x'], 'y', ['x', 'y']]:
            assert_eq(df.drop_duplicates(subset=ss, **kwarg),
                      ddf.drop_duplicates(subset=ss, **kwarg))


def test_set_partition():
    d2 = d.set_partition('b', [0, 2, 9])
    assert d2.divisions == (0, 2, 9)
    expected = full.set_index('b')
    assert_eq(d2, expected)


def test_set_partition_compute():
    d2 = d.set_partition('b', [0, 2, 9])
    d3 = d.set_partition('b', [0, 2, 9], compute=True)

    assert_eq(d2, d3)
    assert_eq(d2, full.set_index('b'))
    assert_eq(d3, full.set_index('b'))
    assert len(d2.dask) > len(d3.dask)

    d4 = d.set_partition(d.b, [0, 2, 9])
    d5 = d.set_partition(d.b, [0, 2, 9], compute=True)
    exp = full.copy()
    exp.index = exp.b
    assert_eq(d4, d5)
    assert_eq(d4, exp)
    assert_eq(d5, exp)
    assert len(d4.dask) > len(d5.dask)


def test_get_partition():
    pdf = pd.DataFrame(np.random.randn(10, 5), columns=list('abcde'))
    ddf = dd.from_pandas(pdf, 3)
    assert ddf.divisions == (0, 4, 8, 9)

    # DataFrame
    div1 = ddf.get_partition(0)
    assert isinstance(div1, dd.DataFrame)
    assert_eq(div1, pdf.loc[0:3])
    div2 = ddf.get_partition(1)
    assert_eq(div2, pdf.loc[4:7])
    div3 = ddf.get_partition(2)
    assert_eq(div3, pdf.loc[8:9])
    assert len(div1) + len(div2) + len(div3) == len(pdf)

    # Series
    div1 = ddf.a.get_partition(0)
    assert isinstance(div1, dd.Series)
    assert_eq(div1, pdf.a.loc[0:3])
    div2 = ddf.a.get_partition(1)
    assert_eq(div2, pdf.a.loc[4:7])
    div3 = ddf.a.get_partition(2)
    assert_eq(div3, pdf.a.loc[8:9])
    assert len(div1) + len(div2) + len(div3) == len(pdf.a)

    with tm.assertRaises(ValueError):
        ddf.get_partition(-1)

    with tm.assertRaises(ValueError):
        ddf.get_partition(3)


def test_ndim():
    assert (d.ndim == 2)
    assert (d.a.ndim == 1)
    assert (d.index.ndim == 1)


def test_dtype():
    assert (d.dtypes == full.dtypes).all()


def test_cache():
    d2 = d.cache()
    assert all(task[0] == getitem for task in d2.dask.values())

    assert_eq(d2.a, d.a)


def test_value_counts():
    df = pd.DataFrame({'x': [1, 2, 1, 3, 3, 1, 4]})
    ddf = dd.from_pandas(df, npartitions=3)
    result = ddf.x.value_counts()
    expected = df.x.value_counts()
    assert_eq(result, expected)
    result2 = ddf.x.value_counts(split_every=2)
    assert_eq(result2, expected)
    assert result._name != result2._name


def test_unique():
    pdf = pd.DataFrame({'x': [1, 2, 1, 3, 3, 1, 4, 2, 3, 1],
                        'y': ['a', 'c', 'b', np.nan, 'c',
                              'b', 'a', 'd', np.nan, 'a']})
    ddf = dd.from_pandas(pdf, npartitions=3)
    assert_eq(ddf.x.unique(), pd.Series(pdf.x.unique(), name='x'))
    assert_eq(ddf.y.unique(), pd.Series(pdf.y.unique(), name='y'))

    assert_eq(ddf.x.unique(split_every=2),
              pd.Series(pdf.x.unique(), name='x'))
    assert_eq(ddf.y.unique(split_every=2),
              pd.Series(pdf.y.unique(), name='y'))
    assert ddf.x.unique(split_every=2)._name != ddf.x.unique()._name


def test_isin():
    assert_eq(d.a.isin([0, 1, 2]), full.a.isin([0, 1, 2]))
    assert_eq(d.a.isin(pd.Series([0, 1, 2])),
              full.a.isin(pd.Series([0, 1, 2])))


def test_len():
    assert len(d) == len(full)
    assert len(d.a) == len(full.a)


def test_size():
    assert_eq(d.size, full.size)
    assert_eq(d.a.size, full.a.size)
    assert_eq(d.index.size, full.index.size)


def test_nbytes():
    assert_eq(d.a.nbytes, full.a.nbytes)
    assert_eq(d.index.nbytes, full.index.nbytes)


def test_quantile():
    # series / multiple
    result = d.b.quantile([.3, .7])
    exp = full.b.quantile([.3, .7])  # result may different
    assert len(result) == 2
    assert result.divisions == (.3, .7)
    assert_eq(result.index, exp.index)
    assert isinstance(result, dd.Series)

    result = result.compute()
    assert isinstance(result, pd.Series)
    assert result.iloc[0] == 0
    assert 5 < result.iloc[1] < 6

    # index
    s = pd.Series(np.arange(10), index=np.arange(10))
    ds = dd.from_pandas(s, 2)

    result = ds.index.quantile([.3, .7])
    exp = s.quantile([.3, .7])
    assert len(result) == 2
    assert result.divisions == (.3, .7)
    assert_eq(result.index, exp.index)
    assert isinstance(result, dd.Series)

    result = result.compute()
    assert isinstance(result, pd.Series)
    assert 1 < result.iloc[0] < 2
    assert 7 < result.iloc[1] < 8

    # series / single
    result = d.b.quantile(.5)
    exp = full.b.quantile(.5)  # result may different
    assert isinstance(result, dd.core.Scalar)
    result = result.compute()
    assert 4 < result < 6


def test_empty_quantile():
    result = d.b.quantile([])
    exp = full.b.quantile([])
    assert result.divisions == (None, None)

    # because of a pandas bug, name is not preserved
    # https://github.com/pydata/pandas/pull/10881
    assert result.name == 'b'
    assert result.compute().name == 'b'
    assert_eq(result, exp, check_names=False)


def test_dataframe_quantile():
    # column X is for test column order and result division
    df = pd.DataFrame({'A': np.arange(20),
                       'X': np.arange(20, 40),
                       'B': np.arange(10, 30),
                       'C': ['a', 'b', 'c', 'd'] * 5},
                      columns=['A', 'X', 'B', 'C'])
    ddf = dd.from_pandas(df, 3)

    result = ddf.quantile()
    assert result.npartitions == 1
    assert result.divisions == ('A', 'X')

    result = result.compute()
    assert isinstance(result, pd.Series)
    tm.assert_index_equal(result.index, pd.Index(['A', 'X', 'B']))
    assert (result > pd.Series([16, 36, 26], index=['A', 'X', 'B'])).all()
    assert (result < pd.Series([17, 37, 27], index=['A', 'X', 'B'])).all()

    result = ddf.quantile([0.25, 0.75])
    assert result.npartitions == 1
    assert result.divisions == (0.25, 0.75)

    result = result.compute()
    assert isinstance(result, pd.DataFrame)
    tm.assert_index_equal(result.index, pd.Index([0.25, 0.75]))
    tm.assert_index_equal(result.columns, pd.Index(['A', 'X', 'B']))
    minexp = pd.DataFrame([[1, 21, 11], [17, 37, 27]],
                          index=[0.25, 0.75], columns=['A', 'X', 'B'])
    assert (result > minexp).all().all()
    maxexp = pd.DataFrame([[2, 22, 12], [18, 38, 28]],
                          index=[0.25, 0.75], columns=['A', 'X', 'B'])
    assert (result < maxexp).all().all()

    assert_eq(ddf.quantile(axis=1), df.quantile(axis=1))
    pytest.raises(ValueError, lambda: ddf.quantile([0.25, 0.75], axis=1))


def test_index():
    assert_eq(d.index, full.index)


def test_assign():
    d_unknown = dd.from_pandas(full, npartitions=3, sort=False)
    assert not d_unknown.known_divisions
    res = d.assign(c=1,
                   d='string',
                   e=d.a.sum(),
                   f=d.a + d.b)
    res_unknown = d_unknown.assign(c=1,
                                   d='string',
                                   e=d_unknown.a.sum(),
                                   f=d_unknown.a + d_unknown.b)
    sol = full.assign(c=1,
                      d='string',
                      e=full.a.sum(),
                      f=full.a + full.b)
    assert_eq(res, sol)
    assert_eq(res_unknown, sol)

    res = d.assign(c=full.a + 1)
    assert_eq(res, full.assign(c=full.a + 1))

    # divisions unknown won't work with pandas
    with pytest.raises(ValueError):
        d_unknown.assign(c=full.a + 1)

    # unsupported type
    with pytest.raises(TypeError):
        d.assign(c=list(range(9)))

    # Fails when assigning known divisions to unknown divisions
    with pytest.raises(ValueError):
        d_unknown.assign(foo=d.a)
    # Fails when assigning unknown divisions to known divisions
    with pytest.raises(ValueError):
        d.assign(foo=d_unknown.a)


def test_map():
    assert_eq(d.a.map(lambda x: x + 1), full.a.map(lambda x: x + 1))
    lk = dict((v, v + 1) for v in full.a.values)
    assert_eq(d.a.map(lk), full.a.map(lk))
    assert_eq(d.b.map(lk), full.b.map(lk))
    lk = pd.Series(lk)
    assert_eq(d.a.map(lk), full.a.map(lk))
    assert_eq(d.b.map(lk), full.b.map(lk))
    assert_eq(d.b.map(lk, meta=d.b), full.b.map(lk))
    assert_eq(d.b.map(lk, meta=('b', 'i8')), full.b.map(lk))
    pytest.raises(TypeError, lambda: d.a.map(d.b))


def test_concat():
    x = _concat([pd.DataFrame(columns=['a', 'b']),
                 pd.DataFrame(columns=['a', 'b'])])
    assert list(x.columns) == ['a', 'b']
    assert len(x) == 0


def test_args():
    e = d.assign(c=d.a + 1)
    f = type(e)(*e._args)
    assert_eq(e, f)
    assert_eq(d.a, type(d.a)(*d.a._args))
    assert_eq(d.a.sum(), type(d.a.sum())(*d.a.sum()._args))


def test_known_divisions():
    assert d.known_divisions
    df = dd.DataFrame(dsk, 'x', meta, divisions=[None, None, None])
    assert not df.known_divisions


def test_unknown_divisions():
    dsk = {('x', 0): pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]}),
           ('x', 1): pd.DataFrame({'a': [4, 5, 6], 'b': [3, 2, 1]}),
           ('x', 2): pd.DataFrame({'a': [7, 8, 9], 'b': [0, 0, 0]})}
    meta = make_meta({'a': 'i8', 'b': 'i8'})
    d = dd.DataFrame(dsk, 'x', meta, [None, None, None, None])
    full = d.compute(get=dask.get)

    assert_eq(d.a.sum(), full.a.sum())
    assert_eq(d.a + d.b + 1, full.a + full.b + 1)


def test_concat2():
    dsk = {('x', 0): pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]}),
           ('x', 1): pd.DataFrame({'a': [4, 5, 6], 'b': [3, 2, 1]}),
           ('x', 2): pd.DataFrame({'a': [7, 8, 9], 'b': [0, 0, 0]})}
    meta = make_meta({'a': 'i8', 'b': 'i8'})
    a = dd.DataFrame(dsk, 'x', meta, [None, None])
    dsk = {('y', 0): pd.DataFrame({'a': [10, 20, 30], 'b': [40, 50, 60]}),
           ('y', 1): pd.DataFrame({'a': [40, 50, 60], 'b': [30, 20, 10]}),
           ('y', 2): pd.DataFrame({'a': [70, 80, 90], 'b': [0, 0, 0]})}
    b = dd.DataFrame(dsk, 'y', meta, [None, None])

    dsk = {('y', 0): pd.DataFrame({'b': [10, 20, 30], 'c': [40, 50, 60]}),
           ('y', 1): pd.DataFrame({'b': [40, 50, 60], 'c': [30, 20, 10]})}
    meta = make_meta({'b': 'i8', 'c': 'i8'})
    c = dd.DataFrame(dsk, 'y', meta, [None, None])

    dsk = {('y', 0): pd.DataFrame({'b': [10, 20, 30], 'c': [40, 50, 60],
                                   'd': [70, 80, 90]}),
           ('y', 1): pd.DataFrame({'b': [40, 50, 60], 'c': [30, 20, 10],
                                   'd': [90, 80, 70]},
                                  index=[3, 4, 5])}
    meta = make_meta({'b': 'i8', 'c': 'i8', 'd': 'i8'},
                     index=pd.Index([], 'i8'))
    d = dd.DataFrame(dsk, 'y', meta, [0, 3, 5])

    cases = [[a, b], [a, c], [a, d]]
    assert dd.concat([a]) is a
    for case in cases:
        result = dd.concat(case)
        pdcase = [_c.compute() for _c in case]

        assert result.npartitions == case[0].npartitions + case[1].npartitions
        assert result.divisions == (None, ) * (result.npartitions + 1)
        assert_eq(pd.concat(pdcase), result)
        assert result.dask == dd.concat(case).dask

        result = dd.concat(case, join='inner')
        assert result.npartitions == case[0].npartitions + case[1].npartitions
        assert result.divisions == (None, ) * (result.npartitions + 1)
        assert_eq(pd.concat(pdcase, join='inner'), result)
        assert result.dask == dd.concat(case, join='inner').dask


def test_concat3():
    pdf1 = pd.DataFrame(np.random.randn(6, 5),
                        columns=list('ABCDE'), index=list('abcdef'))
    pdf2 = pd.DataFrame(np.random.randn(6, 5),
                        columns=list('ABCFG'), index=list('ghijkl'))
    pdf3 = pd.DataFrame(np.random.randn(6, 5),
                        columns=list('ABCHI'), index=list('mnopqr'))
    ddf1 = dd.from_pandas(pdf1, 2)
    ddf2 = dd.from_pandas(pdf2, 3)
    ddf3 = dd.from_pandas(pdf3, 2)

    result = dd.concat([ddf1, ddf2])
    assert result.divisions == ddf1.divisions[:-1] + ddf2.divisions
    assert result.npartitions == ddf1.npartitions + ddf2.npartitions
    assert_eq(result, pd.concat([pdf1, pdf2]))

    assert_eq(dd.concat([ddf1, ddf2], interleave_partitions=True),
              pd.concat([pdf1, pdf2]))

    result = dd.concat([ddf1, ddf2, ddf3])
    assert result.divisions == (ddf1.divisions[:-1] + ddf2.divisions[:-1] +
                                ddf3.divisions)
    assert result.npartitions == (ddf1.npartitions + ddf2.npartitions +
                                  ddf3.npartitions)
    assert_eq(result, pd.concat([pdf1, pdf2, pdf3]))

    assert_eq(dd.concat([ddf1, ddf2, ddf3], interleave_partitions=True),
              pd.concat([pdf1, pdf2, pdf3]))


def test_concat4_interleave_partitions():
    pdf1 = pd.DataFrame(np.random.randn(10, 5),
                        columns=list('ABCDE'), index=list('abcdefghij'))
    pdf2 = pd.DataFrame(np.random.randn(13, 5),
                        columns=list('ABCDE'), index=list('fghijklmnopqr'))
    pdf3 = pd.DataFrame(np.random.randn(13, 6),
                        columns=list('CDEXYZ'), index=list('fghijklmnopqr'))

    ddf1 = dd.from_pandas(pdf1, 2)
    ddf2 = dd.from_pandas(pdf2, 3)
    ddf3 = dd.from_pandas(pdf3, 2)

    msg = ('All inputs have known divisions which cannot be '
           'concatenated in order. Specify '
           'interleave_partitions=True to ignore order')

    cases = [[ddf1, ddf1], [ddf1, ddf2], [ddf1, ddf3], [ddf2, ddf1],
             [ddf2, ddf3], [ddf3, ddf1], [ddf3, ddf2]]
    for case in cases:
        pdcase = [c.compute() for c in case]

        with tm.assertRaisesRegexp(ValueError, msg):
            dd.concat(case)

        assert_eq(dd.concat(case, interleave_partitions=True),
                  pd.concat(pdcase))
        assert_eq(dd.concat(case, join='inner', interleave_partitions=True),
                  pd.concat(pdcase, join='inner'))

    msg = "'join' must be 'inner' or 'outer'"
    with tm.assertRaisesRegexp(ValueError, msg):
        dd.concat([ddf1, ddf1], join='invalid', interleave_partitions=True)


def test_concat5():
    pdf1 = pd.DataFrame(np.random.randn(7, 5),
                        columns=list('ABCDE'), index=list('abcdefg'))
    pdf2 = pd.DataFrame(np.random.randn(7, 6),
                        columns=list('FGHIJK'), index=list('abcdefg'))
    pdf3 = pd.DataFrame(np.random.randn(7, 6),
                        columns=list('FGHIJK'), index=list('cdefghi'))
    pdf4 = pd.DataFrame(np.random.randn(7, 5),
                        columns=list('FGHAB'), index=list('cdefghi'))
    pdf5 = pd.DataFrame(np.random.randn(7, 5),
                        columns=list('FGHAB'), index=list('fklmnop'))

    ddf1 = dd.from_pandas(pdf1, 2)
    ddf2 = dd.from_pandas(pdf2, 3)
    ddf3 = dd.from_pandas(pdf3, 2)
    ddf4 = dd.from_pandas(pdf4, 2)
    ddf5 = dd.from_pandas(pdf5, 3)

    cases = [[ddf1, ddf2], [ddf1, ddf3], [ddf1, ddf4], [ddf1, ddf5],
             [ddf3, ddf4], [ddf3, ddf5], [ddf5, ddf1, ddf4], [ddf5, ddf3],
             [ddf1.A, ddf4.A], [ddf2.F, ddf3.F], [ddf4.A, ddf5.A],
             [ddf1.A, ddf4.F], [ddf2.F, ddf3.H], [ddf4.A, ddf5.B],
             [ddf1, ddf4.A], [ddf3.F, ddf2], [ddf5, ddf1.A, ddf2]]

    for case in cases:
        pdcase = [c.compute() for c in case]

        assert_eq(dd.concat(case, interleave_partitions=True),
                  pd.concat(pdcase))

        assert_eq(dd.concat(case, join='inner', interleave_partitions=True),
                  pd.concat(pdcase, join='inner'))

        assert_eq(dd.concat(case, axis=1), pd.concat(pdcase, axis=1))

        assert_eq(dd.concat(case, axis=1, join='inner'),
                  pd.concat(pdcase, axis=1, join='inner'))

    # Dask + pandas
    cases = [[ddf1, pdf2], [ddf1, pdf3], [pdf1, ddf4],
             [pdf1.A, ddf4.A], [ddf2.F, pdf3.F],
             [ddf1, pdf4.A], [ddf3.F, pdf2], [ddf2, pdf1, ddf3.F]]

    for case in cases:
        pdcase = [c.compute() if isinstance(c, _Frame) else c for c in case]

        assert_eq(dd.concat(case, interleave_partitions=True),
                  pd.concat(pdcase))

        assert_eq(dd.concat(case, join='inner', interleave_partitions=True),
                  pd.concat(pdcase, join='inner'))

        assert_eq(dd.concat(case, axis=1), pd.concat(pdcase, axis=1))

        assert_eq(dd.concat(case, axis=1, join='inner'),
                  pd.concat(pdcase, axis=1, join='inner'))


def test_append():
    df = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6],
                       'b': [1, 2, 3, 4, 5, 6]})
    df2 = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6],
                        'b': [1, 2, 3, 4, 5, 6]},
                       index=[6, 7, 8, 9, 10, 11])
    df3 = pd.DataFrame({'b': [1, 2, 3, 4, 5, 6],
                        'c': [1, 2, 3, 4, 5, 6]},
                       index=[6, 7, 8, 9, 10, 11])

    ddf = dd.from_pandas(df, 2)
    ddf2 = dd.from_pandas(df2, 2)
    ddf3 = dd.from_pandas(df3, 2)

    s = pd.Series([7, 8], name=6, index=['a', 'b'])
    assert_eq(ddf.append(s), df.append(s))

    assert_eq(ddf.append(ddf2), df.append(df2))
    assert_eq(ddf.a.append(ddf2.a), df.a.append(df2.a))
    # different columns
    assert_eq(ddf.append(ddf3), df.append(df3))
    assert_eq(ddf.a.append(ddf3.b), df.a.append(df3.b))

    # dask + pandas
    assert_eq(ddf.append(df2), df.append(df2))
    assert_eq(ddf.a.append(df2.a), df.a.append(df2.a))

    assert_eq(ddf.append(df3), df.append(df3))
    assert_eq(ddf.a.append(df3.b), df.a.append(df3.b))

    df4 = pd.DataFrame({'a': [1, 2, 3, 4, 5, 6],
                        'b': [1, 2, 3, 4, 5, 6]},
                       index=[4, 5, 6, 7, 8, 9])
    ddf4 = dd.from_pandas(df4, 2)
    msg = ("Unable to append two dataframes to each other with known "
           "divisions if those divisions are not ordered. "
           "The divisions/index of the second dataframe must be "
           "greater than the divisions/index of the first dataframe.")
    with tm.assertRaisesRegexp(ValueError, msg):
        ddf.append(ddf4)


def test_append2():
    dsk = {('x', 0): pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]}),
           ('x', 1): pd.DataFrame({'a': [4, 5, 6], 'b': [3, 2, 1]}),
           ('x', 2): pd.DataFrame({'a': [7, 8, 9], 'b': [0, 0, 0]})}
    meta = make_meta({'a': 'i8', 'b': 'i8'})
    ddf1 = dd.DataFrame(dsk, 'x', meta, [None, None])

    dsk = {('y', 0): pd.DataFrame({'a': [10, 20, 30], 'b': [40, 50, 60]}),
           ('y', 1): pd.DataFrame({'a': [40, 50, 60], 'b': [30, 20, 10]}),
           ('y', 2): pd.DataFrame({'a': [70, 80, 90], 'b': [0, 0, 0]})}
    ddf2 = dd.DataFrame(dsk, 'y', meta, [None, None])

    dsk = {('y', 0): pd.DataFrame({'b': [10, 20, 30], 'c': [40, 50, 60]}),
           ('y', 1): pd.DataFrame({'b': [40, 50, 60], 'c': [30, 20, 10]})}
    meta = make_meta({'b': 'i8', 'c': 'i8'})
    ddf3 = dd.DataFrame(dsk, 'y', meta, [None, None])

    assert_eq(ddf1.append(ddf2), ddf1.compute().append(ddf2.compute()))
    assert_eq(ddf2.append(ddf1), ddf2.compute().append(ddf1.compute()))
    # Series + DataFrame
    assert_eq(ddf1.a.append(ddf2), ddf1.a.compute().append(ddf2.compute()))
    assert_eq(ddf2.a.append(ddf1), ddf2.a.compute().append(ddf1.compute()))

    # different columns
    assert_eq(ddf1.append(ddf3), ddf1.compute().append(ddf3.compute()))
    assert_eq(ddf3.append(ddf1), ddf3.compute().append(ddf1.compute()))
    # Series + DataFrame
    assert_eq(ddf1.a.append(ddf3), ddf1.a.compute().append(ddf3.compute()))
    assert_eq(ddf3.b.append(ddf1), ddf3.b.compute().append(ddf1.compute()))

    # Dask + pandas
    assert_eq(ddf1.append(ddf2.compute()), ddf1.compute().append(ddf2.compute()))
    assert_eq(ddf2.append(ddf1.compute()), ddf2.compute().append(ddf1.compute()))
    # Series + DataFrame
    assert_eq(ddf1.a.append(ddf2.compute()), ddf1.a.compute().append(ddf2.compute()))
    assert_eq(ddf2.a.append(ddf1.compute()), ddf2.a.compute().append(ddf1.compute()))

    # different columns
    assert_eq(ddf1.append(ddf3.compute()), ddf1.compute().append(ddf3.compute()))
    assert_eq(ddf3.append(ddf1.compute()), ddf3.compute().append(ddf1.compute()))
    # Series + DataFrame
    assert_eq(ddf1.a.append(ddf3.compute()), ddf1.a.compute().append(ddf3.compute()))
    assert_eq(ddf3.b.append(ddf1.compute()), ddf3.b.compute().append(ddf1.compute()))


@pytest.mark.parametrize('join', ['inner', 'outer', 'left', 'right'])
def test_align(join):
    df1a = pd.DataFrame({'A': np.random.randn(10),
                         'B': np.random.randn(10)},
                        index=[1, 12, 5, 6, 3, 9, 10, 4, 13, 11])

    df1b = pd.DataFrame({'A': np.random.randn(10),
                         'B': np.random.randn(10)},
                        index=[0, 3, 2, 10, 5, 6, 7, 8, 12, 13])
    ddf1a = dd.from_pandas(df1a, 3)
    ddf1b = dd.from_pandas(df1b, 3)

    # DataFrame
    res1, res2 = ddf1a.align(ddf1b, join=join)
    exp1, exp2 = df1a.align(df1b, join=join)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    # Series
    res1, res2 = ddf1a['A'].align(ddf1b['B'], join=join)
    exp1, exp2 = df1a['A'].align(df1b['B'], join=join)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    # DataFrame with fill_value
    res1, res2 = ddf1a.align(ddf1b, join=join, fill_value=1)
    exp1, exp2 = df1a.align(df1b, join=join, fill_value=1)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    # Series
    res1, res2 = ddf1a['A'].align(ddf1b['B'], join=join, fill_value=1)
    exp1, exp2 = df1a['A'].align(df1b['B'], join=join, fill_value=1)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)


@pytest.mark.parametrize('join', ['inner', 'outer', 'left', 'right'])
def test_align_axis(join):
    df1a = pd.DataFrame({'A': np.random.randn(10),
                         'B': np.random.randn(10),
                         'C': np.random.randn(10)},
                        index=[1, 12, 5, 6, 3, 9, 10, 4, 13, 11])

    df1b = pd.DataFrame({'B': np.random.randn(10),
                         'C': np.random.randn(10),
                         'D': np.random.randn(10)},
                        index=[0, 3, 2, 10, 5, 6, 7, 8, 12, 13])
    ddf1a = dd.from_pandas(df1a, 3)
    ddf1b = dd.from_pandas(df1b, 3)

    res1, res2 = ddf1a.align(ddf1b, join=join, axis=0)
    exp1, exp2 = df1a.align(df1b, join=join, axis=0)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    res1, res2 = ddf1a.align(ddf1b, join=join, axis=1)
    exp1, exp2 = df1a.align(df1b, join=join, axis=1)
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    res1, res2 = ddf1a.align(ddf1b, join=join, axis='index')
    exp1, exp2 = df1a.align(df1b, join=join, axis='index')
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    res1, res2 = ddf1a.align(ddf1b, join=join, axis='columns')
    exp1, exp2 = df1a.align(df1b, join=join, axis='columns')
    assert assert_eq(res1, exp1)
    assert assert_eq(res2, exp2)

    # invalid
    with tm.assertRaises(ValueError):
        ddf1a.align(ddf1b, join=join, axis='XXX')

    with tm.assertRaises(ValueError):
        ddf1a['A'].align(ddf1b['B'], join=join, axis=1)


def test_combine():
    df1 = pd.DataFrame({'A': np.random.choice([1, 2, np.nan], 100),
                        'B': np.random.choice(['a', 'b', np.nan], 100)})

    df2 = pd.DataFrame({'A': np.random.choice([1, 2, 3], 100),
                        'B': np.random.choice(['a', 'b', 'c'], 100)})
    ddf1 = dd.from_pandas(df1, 4)
    ddf2 = dd.from_pandas(df2, 5)

    first = lambda a, b: a

    # DataFrame
    for da, db, a, b in [(ddf1, ddf2, df1, df2),
                         (ddf1.A, ddf2.A, df1.A, df2.A),
                         (ddf1.B, ddf2.B, df1.B, df2.B)]:
        for func, fill_value in [(add, None), (add, 100), (first, None)]:
            sol = a.combine(b, func, fill_value=fill_value)
            assert_eq(da.combine(db, func, fill_value=fill_value), sol)
            assert_eq(da.combine(b, func, fill_value=fill_value), sol)

    assert_eq(ddf1.combine(ddf2, add, overwrite=False),
              df1.combine(df2, add, overwrite=False))
    assert da.combine(db, add)._name == da.combine(db, add)._name


def test_combine_first():
    df1 = pd.DataFrame({'A': np.random.choice([1, 2, np.nan], 100),
                        'B': np.random.choice(['a', 'b', np.nan], 100)})

    df2 = pd.DataFrame({'A': np.random.choice([1, 2, 3], 100),
                        'B': np.random.choice(['a', 'b', 'c'], 100)})
    ddf1 = dd.from_pandas(df1, 4)
    ddf2 = dd.from_pandas(df2, 5)

    # DataFrame
    assert_eq(ddf1.combine_first(ddf2), df1.combine_first(df2))
    assert_eq(ddf1.combine_first(df2), df1.combine_first(df2))

    # Series
    assert_eq(ddf1.A.combine_first(ddf2.A), df1.A.combine_first(df2.A))
    assert_eq(ddf1.A.combine_first(df2.A), df1.A.combine_first(df2.A))

    assert_eq(ddf1.B.combine_first(ddf2.B), df1.B.combine_first(df2.B))
    assert_eq(ddf1.B.combine_first(df2.B), df1.B.combine_first(df2.B))


def test_dataframe_picklable():
    from pickle import loads, dumps
    cloudpickle = pytest.importorskip('cloudpickle')
    cp_dumps = cloudpickle.dumps

    d = tm.makeTimeDataFrame()
    df = dd.from_pandas(d, npartitions=3)
    df = df + 2

    # dataframe
    df2 = loads(dumps(df))
    assert_eq(df, df2)
    df2 = loads(cp_dumps(df))
    assert_eq(df, df2)

    # series
    a2 = loads(dumps(df.A))
    assert_eq(df.A, a2)
    a2 = loads(cp_dumps(df.A))
    assert_eq(df.A, a2)

    # index
    i2 = loads(dumps(df.index))
    assert_eq(df.index, i2)
    i2 = loads(cp_dumps(df.index))
    assert_eq(df.index, i2)

    # scalar
    # lambdas are present, so only test cloudpickle
    s = df.A.sum()
    s2 = loads(cp_dumps(s))
    assert_eq(s, s2)


def test_random_partitions():
    a, b = d.random_split([0.5, 0.5], 42)
    assert isinstance(a, dd.DataFrame)
    assert isinstance(b, dd.DataFrame)
    assert a._name != b._name

    assert len(a.compute()) + len(b.compute()) == len(full)
    a2, b2 = d.random_split([0.5, 0.5], 42)
    assert a2._name == a._name
    assert b2._name == b._name

    parts = d.random_split([0.4, 0.5, 0.1], 42)
    names = set([p._name for p in parts])
    names.update([a._name, b._name])
    assert len(names) == 5

    with pytest.raises(ValueError):
        d.random_split([0.4, 0.5], 42)


def test_series_round():
    ps = pd.Series([1.123, 2.123, 3.123, 1.234, 2.234, 3.234], name='a')
    s = dd.from_pandas(ps, npartitions=3)
    assert_eq(s.round(), ps.round())


def test_set_partition_2():
    df = pd.DataFrame({'x': [1, 2, 3, 4, 5, 6], 'y': list('abdabd')})
    ddf = dd.from_pandas(df, 2)

    result = ddf.set_partition('y', ['a', 'c', 'd'])
    assert result.divisions == ('a', 'c', 'd')

    assert list(result.compute(get=get_sync).index[-2:]) == ['d', 'd']


@pytest.mark.slow
def test_repartition():
    def _check_split_data(orig, d):
        """Check data is split properly"""
        keys = [k for k in d.dask if k[0].startswith('repartition-split')]
        keys = sorted(keys)
        sp = pd.concat([d._get(d.dask, k) for k in keys])
        assert_eq(orig, sp)
        assert_eq(orig, d)

    df = pd.DataFrame({'x': [1, 2, 3, 4, 5, 6], 'y': list('abdabd')},
                      index=[10, 20, 30, 40, 50, 60])
    a = dd.from_pandas(df, 2)

    b = a.repartition(divisions=[10, 20, 50, 60])
    assert b.divisions == (10, 20, 50, 60)
    assert_eq(a, b)
    assert_eq(a._get(b.dask, (b._name, 0)), df.iloc[:1])

    for div in [[20, 60], [10, 50], [1],   # first / last element mismatch
                [0, 60], [10, 70],   # do not allow to expand divisions by default
                [10, 50, 20, 60],    # not sorted
                [10, 10, 20, 60]]:   # not unique (last element can be duplicated)

        pytest.raises(ValueError, lambda: a.repartition(divisions=div))

    pdf = pd.DataFrame(np.random.randn(7, 5), columns=list('abxyz'))
    for p in range(1, 7):
        ddf = dd.from_pandas(pdf, p)
        assert_eq(ddf, pdf)
        for div in [[0, 6], [0, 6, 6], [0, 5, 6], [0, 4, 6, 6],
                    [0, 2, 6], [0, 2, 6, 6],
                    [0, 2, 3, 6, 6], [0, 1, 2, 3, 4, 5, 6, 6]]:
            rddf = ddf.repartition(divisions=div)
            _check_split_data(ddf, rddf)
            assert rddf.divisions == tuple(div)
            assert_eq(pdf, rddf)

            rds = ddf.x.repartition(divisions=div)
            _check_split_data(ddf.x, rds)
            assert rds.divisions == tuple(div)
            assert_eq(pdf.x, rds)

        # expand divisions
        for div in [[-5, 10], [-2, 3, 5, 6], [0, 4, 5, 9, 10]]:
            rddf = ddf.repartition(divisions=div, force=True)
            _check_split_data(ddf, rddf)
            assert rddf.divisions == tuple(div)
            assert_eq(pdf, rddf)

            rds = ddf.x.repartition(divisions=div, force=True)
            _check_split_data(ddf.x, rds)
            assert rds.divisions == tuple(div)
            assert_eq(pdf.x, rds)

    pdf = pd.DataFrame({'x': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
                        'y': [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]},
                       index=list('abcdefghij'))
    for p in range(1, 7):
        ddf = dd.from_pandas(pdf, p)
        assert_eq(ddf, pdf)
        for div in [list('aj'), list('ajj'), list('adj'),
                    list('abfj'), list('ahjj'), list('acdj'), list('adfij'),
                    list('abdefgij'), list('abcdefghij')]:
            rddf = ddf.repartition(divisions=div)
            _check_split_data(ddf, rddf)
            assert rddf.divisions == tuple(div)
            assert_eq(pdf, rddf)

            rds = ddf.x.repartition(divisions=div)
            _check_split_data(ddf.x, rds)
            assert rds.divisions == tuple(div)
            assert_eq(pdf.x, rds)

        # expand divisions
        for div in [list('Yadijm'), list('acmrxz'), list('Yajz')]:
            rddf = ddf.repartition(divisions=div, force=True)
            _check_split_data(ddf, rddf)
            assert rddf.divisions == tuple(div)
            assert_eq(pdf, rddf)

            rds = ddf.x.repartition(divisions=div, force=True)
            _check_split_data(ddf.x, rds)
            assert rds.divisions == tuple(div)
            assert_eq(pdf.x, rds)


def test_repartition_divisions():
    result = repartition_divisions([0, 6], [0, 6, 6], 'a', 'b', 'c')
    assert result == {('b', 0): (boundary_slice, ('a', 0), 0, 6, False),
                      ('b', 1): (boundary_slice, ('a', 0), 6, 6, True),
                      ('c', 0): ('b', 0),
                      ('c', 1): ('b', 1)}

    result = repartition_divisions([1, 3, 7], [1, 4, 6, 7], 'a', 'b', 'c')
    assert result == {('b', 0): (boundary_slice, ('a', 0), 1, 3, False),
                      ('b', 1): (boundary_slice, ('a', 1), 3, 4, False),
                      ('b', 2): (boundary_slice, ('a', 1), 4, 6, False),
                      ('b', 3): (boundary_slice, ('a', 1), 6, 7, True),
                      ('c', 0): (pd.concat, [('b', 0), ('b', 1)]),
                      ('c', 1): ('b', 2),
                      ('c', 2): ('b', 3)}


def test_repartition_on_pandas_dataframe():
    df = pd.DataFrame({'x': [1, 2, 3, 4, 5, 6], 'y': list('abdabd')},
                      index=[10, 20, 30, 40, 50, 60])
    ddf = dd.repartition(df, divisions=[10, 20, 50, 60])
    assert isinstance(ddf, dd.DataFrame)
    assert ddf.divisions == (10, 20, 50, 60)
    assert_eq(ddf, df)

    ddf = dd.repartition(df.y, divisions=[10, 20, 50, 60])
    assert isinstance(ddf, dd.Series)
    assert ddf.divisions == (10, 20, 50, 60)
    assert_eq(ddf, df.y)


def test_repartition_npartitions():
    for use_index in (True, False):
        df = pd.DataFrame({'x': [1, 2, 3, 4, 5, 6], 'y': list('abdabd')},
                          index=[10, 20, 30, 40, 50, 60])
        for n in [1, 2, 4, 5]:
            for k in [1, 2, 4, 5]:
                if k > n:
                    continue
                a = dd.from_pandas(df, npartitions=n, sort=use_index)
                k = min(a.npartitions, k)

                b = a.repartition(npartitions=k)
                assert_eq(a, b)
                assert b.npartitions == k

        a = dd.from_pandas(df, npartitions=1)
        with pytest.raises(ValueError):
            a.repartition(npartitions=5)


def test_embarrassingly_parallel_operations():
    df = pd.DataFrame({'x': [1, 2, 3, 4, None, 6], 'y': list('abdabd')},
                      index=[10, 20, 30, 40, 50, 60])
    a = dd.from_pandas(df, 2)

    assert_eq(a.x.astype('float32'), df.x.astype('float32'))
    assert a.x.astype('float32').compute().dtype == 'float32'

    assert_eq(a.x.dropna(), df.x.dropna())

    assert_eq(a.x.between(2, 4), df.x.between(2, 4))

    assert_eq(a.x.clip(2, 4), df.x.clip(2, 4))

    assert_eq(a.x.notnull(), df.x.notnull())
    assert_eq(a.x.isnull(), df.x.isnull())
    assert_eq(a.notnull(), df.notnull())
    assert_eq(a.isnull(), df.isnull())

    assert len(a.sample(0.5).compute()) < len(df)


def test_fillna():
    df = tm.makeMissingDataframe(0.8, 42)
    ddf = dd.from_pandas(df, npartitions=5, sort=False)

    assert_eq(ddf.fillna(100), df.fillna(100))
    assert_eq(ddf.A.fillna(100), df.A.fillna(100))

    assert_eq(ddf.fillna(method='pad'), df.fillna(method='pad'))
    assert_eq(ddf.A.fillna(method='pad'), df.A.fillna(method='pad'))

    assert_eq(ddf.fillna(method='bfill'), df.fillna(method='bfill'))
    assert_eq(ddf.A.fillna(method='bfill'), df.A.fillna(method='bfill'))

    assert_eq(ddf.fillna(method='pad', limit=2),
              df.fillna(method='pad', limit=2))
    assert_eq(ddf.A.fillna(method='pad', limit=2),
              df.A.fillna(method='pad', limit=2))

    assert_eq(ddf.fillna(method='bfill', limit=2),
              df.fillna(method='bfill', limit=2))
    assert_eq(ddf.A.fillna(method='bfill', limit=2),
              df.A.fillna(method='bfill', limit=2))

    assert_eq(ddf.fillna(100, axis=1), df.fillna(100, axis=1))
    assert_eq(ddf.fillna(method='pad', axis=1), df.fillna(method='pad', axis=1))
    assert_eq(ddf.fillna(method='pad', limit=2, axis=1),
              df.fillna(method='pad', limit=2, axis=1))

    pytest.raises(ValueError, lambda: ddf.A.fillna(0, axis=1))
    pytest.raises(NotImplementedError, lambda: ddf.fillna(0, limit=10))
    pytest.raises(NotImplementedError, lambda: ddf.fillna(0, limit=10, axis=1))

    df = tm.makeMissingDataframe(0.2, 42)
    ddf = dd.from_pandas(df, npartitions=5, sort=False)
    pytest.raises(ValueError, lambda: ddf.fillna(method='pad').compute())
    assert_eq(df.fillna(method='pad', limit=3),
              ddf.fillna(method='pad', limit=3))


def test_sample():
    df = pd.DataFrame({'x': [1, 2, 3, 4, None, 6], 'y': list('abdabd')},
                      index=[10, 20, 30, 40, 50, 60])
    a = dd.from_pandas(df, 2)

    b = a.sample(0.5)

    assert_eq(b, b)

    c = a.sample(0.5, random_state=1234)
    d = a.sample(0.5, random_state=1234)
    assert_eq(c, d)

    assert a.sample(0.5)._name != a.sample(0.5)._name


def test_sample_without_replacement():
    df = pd.DataFrame({'x': [1, 2, 3, 4, None, 6], 'y': list('abdabd')},
                      index=[10, 20, 30, 40, 50, 60])
    a = dd.from_pandas(df, 2)
    b = a.sample(0.7, replace=False)
    bb = b.index.compute()
    assert len(bb) == len(set(bb))


def test_datetime_accessor():
    df = pd.DataFrame({'x': [1, 2, 3, 4]})
    df['x'] = df.x.astype('M8[us]')

    a = dd.from_pandas(df, 2)

    assert 'date' in dir(a.x.dt)

    # pandas loses Series.name via datetime accessor
    # see https://github.com/pydata/pandas/issues/10712
    assert_eq(a.x.dt.date, df.x.dt.date, check_names=False)
    assert (a.x.dt.to_pydatetime().compute() == df.x.dt.to_pydatetime()).all()

    assert set(a.x.dt.date.dask) == set(a.x.dt.date.dask)
    assert set(a.x.dt.to_pydatetime().dask) == set(a.x.dt.to_pydatetime().dask)


def test_str_accessor():
    df = pd.DataFrame({'x': ['a', 'b', 'c', 'D']}, index=['e', 'f', 'g', 'H'])

    a = dd.from_pandas(df, 2, sort=False)

    assert 'upper' in dir(a.x.str)
    assert_eq(a.x.str.upper(), df.x.str.upper())
    assert set(a.x.str.upper().dask) == set(a.x.str.upper().dask)

    assert 'upper' in dir(a.index.str)
    assert_eq(a.index.str.upper(), df.index.str.upper())
    assert set(a.index.str.upper().dask) == set(a.index.str.upper().dask)

    # make sure to pass thru args & kwargs
    assert 'contains' in dir(a.x.str)
    assert_eq(a.x.str.contains('a'), df.x.str.contains('a'))
    assert set(a.x.str.contains('a').dask) == set(a.x.str.contains('a').dask)

    assert_eq(a.x.str.contains('d', case=False), df.x.str.contains('d', case=False))
    assert set(a.x.str.contains('d', case=False).dask) == set(a.x.str.contains('d', case=False).dask)

    for na in [True, False]:
        assert_eq(a.x.str.contains('a', na=na), df.x.str.contains('a', na=na))
        assert set(a.x.str.contains('a', na=na).dask) == set(a.x.str.contains('a', na=na).dask)

    for regex in [True, False]:
        assert_eq(a.x.str.contains('a', regex=regex), df.x.str.contains('a', regex=regex))
        assert set(a.x.str.contains('a', regex=regex).dask) == set(a.x.str.contains('a', regex=regex).dask)


def test_empty_max():
    meta = make_meta({'x': 'i8'})
    a = dd.DataFrame({('x', 0): pd.DataFrame({'x': [1]}),
                      ('x', 1): pd.DataFrame({'x': []})}, 'x',
                     meta, [None, None, None])
    assert_eq(a.x.max(), 1)


def test_query():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    a = dd.from_pandas(df, npartitions=2)
    q = a.query('x**2 > y')
    with ignoring(ImportError):
        assert_eq(q, df.query('x**2 > y'))


def test_eval():
    p = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    d = dd.from_pandas(p, npartitions=2)
    with ignoring(ImportError):
        assert_eq(p.eval('x + y'), d.eval('x + y'))
        assert_eq(p.eval('z = x + y', inplace=False),
                  d.eval('z = x + y', inplace=False))
        with pytest.raises(NotImplementedError):
            d.eval('z = x + y', inplace=True)

        if p.eval('z = x + y', inplace=None) is None:
            with pytest.raises(NotImplementedError):
                d.eval('z = x + y', inplace=None)


@pytest.mark.parametrize('include, exclude', [
    ([int], None),
    (None, [int]),
    ([np.number, object], [float]),
    (['datetime'], None)
])
def test_select_dtypes(include, exclude):
    n = 10
    df = pd.DataFrame({'cint': [1] * n,
                       'cstr': ['a'] * n,
                       'clfoat': [1.] * n,
                       'cdt': pd.date_range('2016-01-01', periods=n)})
    a = dd.from_pandas(df, npartitions=2)
    result = a.select_dtypes(include=include, exclude=exclude)
    expected = df.select_dtypes(include=include, exclude=exclude)
    assert_eq(result, expected)

    # count dtypes
    tm.assert_series_equal(a.get_dtype_counts(), df.get_dtype_counts())
    tm.assert_series_equal(a.get_ftype_counts(), df.get_ftype_counts())

    tm.assert_series_equal(result.get_dtype_counts(),
                           expected.get_dtype_counts())
    tm.assert_series_equal(result.get_ftype_counts(),
                           expected.get_ftype_counts())


def test_deterministic_apply_concat_apply_names():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [5, 6, 7, 8]})
    a = dd.from_pandas(df, npartitions=2)

    assert sorted(a.x.nlargest(2).dask) == sorted(a.x.nlargest(2).dask)
    assert sorted(a.x.nlargest(2).dask) != sorted(a.x.nlargest(3).dask)
    assert (sorted(a.x.drop_duplicates().dask) ==
            sorted(a.x.drop_duplicates().dask))
    assert (sorted(a.groupby('x').y.mean().dask) ==
            sorted(a.groupby('x').y.mean().dask))

    # Test aca without passing in token string
    f = lambda a: a.nlargest(5)
    f2 = lambda a: a.nlargest(3)
    assert (sorted(aca(a.x, f, f, a.x._meta).dask) !=
            sorted(aca(a.x, f2, f2, a.x._meta).dask))
    assert (sorted(aca(a.x, f, f, a.x._meta).dask) ==
            sorted(aca(a.x, f, f, a.x._meta).dask))

    # Test aca with keywords
    def chunk(x, c_key=0, both_key=0):
        return x.sum() + c_key + both_key

    def agg(x, a_key=0, both_key=0):
        return pd.Series(x).sum() + a_key + both_key

    c_key = 2
    a_key = 3
    both_key = 4

    res = aca(a.x, chunk=chunk, aggregate=agg, chunk_kwargs={'c_key': c_key},
              aggregate_kwargs={'a_key': a_key}, both_key=both_key)
    assert (sorted(res.dask) ==
            sorted(aca(a.x, chunk=chunk, aggregate=agg,
                       chunk_kwargs={'c_key': c_key},
                       aggregate_kwargs={'a_key': a_key},
                       both_key=both_key).dask))
    assert (sorted(res.dask) !=
            sorted(aca(a.x, chunk=chunk, aggregate=agg,
                       chunk_kwargs={'c_key': c_key},
                       aggregate_kwargs={'a_key': a_key},
                       both_key=0).dask))

    assert_eq(res, df.x.sum() + 2 * (c_key + both_key) + a_key + both_key)


def test_aca_meta_infer():
    df = pd.DataFrame({'x': [1, 2, 3, 4],
                       'y': [5, 6, 7, 8]})
    ddf = dd.from_pandas(df, npartitions=2)

    def chunk(x, y, constant=1.0):
        return (x + y + constant).head()

    def agg(x):
        return x.head()

    res = aca([ddf, 2.0], chunk=chunk, aggregate=agg,
              chunk_kwargs=dict(constant=2.0))
    sol = (df + 2.0 + 2.0).head()
    assert_eq(res, sol)

    # Should infer as a scalar
    res = aca([ddf.x], chunk=lambda x: pd.Series([x.sum()]),
              aggregate=lambda x: x.sum())
    assert isinstance(res, Scalar)
    assert res.compute() == df.x.sum()


def test_aca_split_every():
    df = pd.DataFrame({'x': [1] * 60})
    ddf = dd.from_pandas(df, npartitions=15)

    def chunk(x, y, constant=0):
        return x.sum() + y + constant

    def combine(x, constant=0):
        return x.sum() + constant + 1

    def agg(x, constant=0):
        return x.sum() + constant + 2

    f = lambda n: aca([ddf, 2.0], chunk=chunk, aggregate=agg, combine=combine,
                      chunk_kwargs=dict(constant=1.0),
                      combine_kwargs=dict(constant=2.0),
                      aggregate_kwargs=dict(constant=3.0),
                      split_every=n)

    assert_max_deps(f(3), 3)
    assert_max_deps(f(4), 4, False)
    assert_max_deps(f(5), 5)
    assert set(f(15).dask.keys()) == set(f(ddf.npartitions).dask.keys())

    r3 = f(3)
    r4 = f(4)
    assert r3._name != r4._name
    # Only intersect on reading operations
    assert len(set(r3.dask.keys()) & set(r4.dask.keys())) == len(ddf.dask.keys())

    # Keywords are different for each step
    assert f(3).compute() == 60 + 15 * (2 + 1) + 7 * (2 + 1) + (3 + 2)
    # Keywords are same for each step
    res = aca([ddf, 2.0], chunk=chunk, aggregate=agg, combine=combine,
              constant=3.0, split_every=3)
    assert res.compute() == 60 + 15 * (2 + 3) + 7 * (3 + 1) + (3 + 2)
    # No combine provided, combine is agg
    res = aca([ddf, 2.0], chunk=chunk, aggregate=agg, constant=3, split_every=3)
    assert res.compute() == 60 + 15 * (2 + 3) + 8 * (3 + 2)

    # split_every must be >= 2
    with pytest.raises(ValueError):
        f(1)

    # combine_kwargs with no combine provided
    with pytest.raises(ValueError):
        aca([ddf, 2.0], chunk=chunk, aggregate=agg, split_every=3,
            chunk_kwargs=dict(constant=1.0),
            combine_kwargs=dict(constant=2.0),
            aggregate_kwargs=dict(constant=3.0))


def test_reduction_method():
    df = pd.DataFrame({'x': range(50), 'y': range(50, 100)})
    ddf = dd.from_pandas(df, npartitions=4)

    chunk = lambda x, val=0: (x >= val).sum()
    agg = lambda x: x.sum()

    # Output of chunk is a scalar
    res = ddf.x.reduction(chunk, aggregate=agg)
    assert_eq(res, df.x.count())

    # Output of chunk is a series
    res = ddf.reduction(chunk, aggregate=agg)
    assert res._name == ddf.reduction(chunk, aggregate=agg)._name
    assert_eq(res, df.count())

    # Test with keywords
    res2 = ddf.reduction(chunk, aggregate=agg, chunk_kwargs={'val': 25})
    res2._name == ddf.reduction(chunk, aggregate=agg,
                                chunk_kwargs={'val': 25})._name
    assert res2._name != res._name
    assert_eq(res2, (df >= 25).sum())

    # Output of chunk is a dataframe
    def sum_and_count(x):
        return pd.DataFrame({'sum': x.sum(), 'count': x.count()})
    res = ddf.reduction(sum_and_count,
                        aggregate=lambda x: x.groupby(level=0).sum())

    assert_eq(res, pd.DataFrame({'sum': df.sum(), 'count': df.count()}))


def test_reduction_method_split_every():
    df = pd.Series([1] * 60)
    ddf = dd.from_pandas(df, npartitions=15)

    def chunk(x, constant=0):
        return x.sum() + constant

    def combine(x, constant=0):
        return x.sum() + constant + 1

    def agg(x, constant=0):
        return x.sum() + constant + 2

    f = lambda n: ddf.reduction(chunk, aggregate=agg, combine=combine,
                                chunk_kwargs=dict(constant=1.0),
                                combine_kwargs=dict(constant=2.0),
                                aggregate_kwargs=dict(constant=3.0),
                                split_every=n)

    assert_max_deps(f(3), 3)
    assert_max_deps(f(4), 4, False)
    assert_max_deps(f(5), 5)
    assert set(f(15).dask.keys()) == set(f(ddf.npartitions).dask.keys())

    r3 = f(3)
    r4 = f(4)
    assert r3._name != r4._name
    # Only intersect on reading operations
    assert len(set(r3.dask.keys()) & set(r4.dask.keys())) == len(ddf.dask.keys())

    # Keywords are different for each step
    assert f(3).compute() == 60 + 15 + 7 * (2 + 1) + (3 + 2)
    # Keywords are same for each step
    res = ddf.reduction(chunk, aggregate=agg, combine=combine, constant=3.0,
                        split_every=3)
    assert res.compute() == 60 + 15 * 3 + 7 * (3 + 1) + (3 + 2)
    # No combine provided, combine is agg
    res = ddf.reduction(chunk, aggregate=agg, constant=3.0, split_every=3)
    assert res.compute() == 60 + 15 * 3 + 8 * (3 + 2)

    # split_every must be >= 2
    with pytest.raises(ValueError):
        f(1)

    # combine_kwargs with no combine provided
    with pytest.raises(ValueError):
        ddf.reduction(chunk, aggregate=agg, split_every=3,
                      chunk_kwargs=dict(constant=1.0),
                      combine_kwargs=dict(constant=2.0),
                      aggregate_kwargs=dict(constant=3.0))


def test_pipe():
    df = pd.DataFrame({'x': range(50), 'y': range(50, 100)})
    ddf = dd.from_pandas(df, npartitions=4)

    def f(x, y, z=0):
        return x + y + z

    assert_eq(ddf.pipe(f, 1, z=2), f(ddf, 1, z=2))
    assert_eq(ddf.x.pipe(f, 1, z=2), f(ddf.x, 1, z=2))


def test_gh_517():
    arr = np.random.randn(100, 2)
    df = pd.DataFrame(arr, columns=['a', 'b'])
    ddf = dd.from_pandas(df, 2)
    assert ddf.index.nunique().compute() == 100

    ddf2 = dd.from_pandas(pd.concat([df, df]), 5)
    assert ddf2.index.nunique().compute() == 100


def test_drop_axis_1():
    df = pd.DataFrame({'x': [1, 2, 3, 4],
                       'y': [5, 6, 7, 8],
                       'z': [9, 10, 11, 12]})
    ddf = dd.from_pandas(df, npartitions=2)

    assert_eq(ddf.drop('y', axis=1), df.drop('y', axis=1))
    assert_eq(ddf.drop(['y', 'z'], axis=1), df.drop(['y', 'z'], axis=1))
    with pytest.raises(ValueError):
        ddf.drop(['a', 'x'], axis=1)
    assert_eq(ddf.drop(['a', 'x'], axis=1, errors='ignore'),
              df.drop(['a', 'x'], axis=1, errors='ignore'))


def test_gh580():
    df = pd.DataFrame({'x': np.arange(10, dtype=float)})
    ddf = dd.from_pandas(df, 2)
    assert_eq(np.cos(df['x']), np.cos(ddf['x']))
    assert_eq(np.cos(df['x']), np.cos(ddf['x']))


def test_rename_dict():
    renamer = {'a': 'A', 'b': 'B'}
    assert_eq(d.rename(columns=renamer),
              full.rename(columns=renamer))


def test_rename_function():
    renamer = lambda x: x.upper()
    assert_eq(d.rename(columns=renamer),
              full.rename(columns=renamer))


def test_rename_index():
    renamer = {0: 1}
    pytest.raises(ValueError, lambda: d.rename(index=renamer))


def test_to_timestamp():
    index = pd.PeriodIndex(freq='A', start='1/1/2001', end='12/1/2004')
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]}, index=index)
    ddf = dd.from_pandas(df, npartitions=3)
    assert_eq(ddf.to_timestamp(), df.to_timestamp())
    assert_eq(ddf.to_timestamp(freq='M', how='s').compute(),
              df.to_timestamp(freq='M', how='s'))
    assert_eq(ddf.x.to_timestamp(), df.x.to_timestamp())
    assert_eq(ddf.x.to_timestamp(freq='M', how='s').compute(),
              df.x.to_timestamp(freq='M', how='s'))


def test_to_frame():
    s = pd.Series([1, 2, 3], name='foo')
    a = dd.from_pandas(s, npartitions=2)

    assert_eq(s.to_frame(), a.to_frame())
    assert_eq(s.to_frame('bar'), a.to_frame('bar'))


def test_apply():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)

    func = lambda row: row['x'] + row['y']
    assert_eq(ddf.x.apply(lambda x: x + 1),
              df.x.apply(lambda x: x + 1))

    # specify columns
    assert_eq(ddf.apply(lambda xy: xy[0] + xy[1], axis=1, columns=None),
              df.apply(lambda xy: xy[0] + xy[1], axis=1))
    assert_eq(ddf.apply(lambda xy: xy[0] + xy[1], axis='columns', columns=None),
              df.apply(lambda xy: xy[0] + xy[1], axis='columns'))

    # inference
    assert_eq(ddf.apply(lambda xy: xy[0] + xy[1], axis=1),
              df.apply(lambda xy: xy[0] + xy[1], axis=1))
    assert_eq(ddf.apply(lambda xy: xy, axis=1),
              df.apply(lambda xy: xy, axis=1))

    # result will be dataframe
    func = lambda x: pd.Series([x, x])
    assert_eq(ddf.x.apply(func, name=[0, 1]), df.x.apply(func))
    # inference
    assert_eq(ddf.x.apply(func), df.x.apply(func))

    # axis=0
    with tm.assertRaises(NotImplementedError):
        ddf.apply(lambda xy: xy, axis=0)

    with tm.assertRaises(NotImplementedError):
        ddf.apply(lambda xy: xy, axis='index')


def test_applymap():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)
    assert_eq(ddf.applymap(lambda x: x + 1), df.applymap(lambda x: x + 1))

    assert_eq(ddf.applymap(lambda x: (x, x)), df.applymap(lambda x: (x, x)))


def test_abs():
    df = pd.DataFrame({'A': [1, -2, 3, -4, 5],
                       'B': [-6., -7, -8, -9, 10],
                       'C': ['a', 'b', 'c', 'd', 'e']})
    ddf = dd.from_pandas(df, npartitions=2)
    assert_eq(ddf.A.abs(), df.A.abs())
    assert_eq(ddf[['A', 'B']].abs(), df[['A', 'B']].abs())
    pytest.raises(TypeError, lambda: ddf.C.abs())
    pytest.raises(TypeError, lambda: ddf.abs())


def test_round():
    df = pd.DataFrame({'col1': [1.123, 2.123, 3.123],
                       'col2': [1.234, 2.234, 3.234]})
    ddf = dd.from_pandas(df, npartitions=2)
    assert_eq(ddf.round(), df.round())
    assert_eq(ddf.round(2), df.round(2))


def test_cov():
    # DataFrame
    df = pd.util.testing.makeMissingDataframe(0.3, 42)
    ddf = dd.from_pandas(df, npartitions=6)

    res = ddf.cov()
    res2 = ddf.cov(split_every=2)
    res3 = ddf.cov(10)
    res4 = ddf.cov(10, split_every=2)
    sol = df.cov()
    sol2 = df.cov(10)
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert_eq(res3, sol2)
    assert_eq(res4, sol2)
    assert res._name == ddf.cov()._name
    assert res._name != res2._name
    assert res3._name != res4._name
    assert res._name != res3._name

    # Series
    a = df.A
    b = df.B
    da = dd.from_pandas(a, npartitions=6)
    db = dd.from_pandas(b, npartitions=7)

    res = da.cov(db)
    res2 = da.cov(db, split_every=2)
    res3 = da.cov(db, 10)
    res4 = da.cov(db, 10, split_every=2)
    sol = a.cov(b)
    sol2 = a.cov(b, 10)
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert_eq(res3, sol2)
    assert_eq(res4, sol2)
    assert res._name == da.cov(db)._name
    assert res._name != res2._name
    assert res3._name != res4._name
    assert res._name != res3._name


def test_corr():
    # DataFrame
    df = pd.util.testing.makeMissingDataframe(0.3, 42)
    ddf = dd.from_pandas(df, npartitions=6)

    res = ddf.corr()
    res2 = ddf.corr(split_every=2)
    res3 = ddf.corr(min_periods=10)
    res4 = ddf.corr(min_periods=10, split_every=2)
    sol = df.corr()
    sol2 = df.corr(min_periods=10)
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert_eq(res3, sol2)
    assert_eq(res4, sol2)
    assert res._name == ddf.corr()._name
    assert res._name != res2._name
    assert res3._name != res4._name
    assert res._name != res3._name

    pytest.raises(NotImplementedError, lambda: ddf.corr(method='spearman'))

    # Series
    a = df.A
    b = df.B
    da = dd.from_pandas(a, npartitions=6)
    db = dd.from_pandas(b, npartitions=7)

    res = da.corr(db)
    res2 = da.corr(db, split_every=2)
    res3 = da.corr(db, min_periods=10)
    res4 = da.corr(db, min_periods=10, split_every=2)
    sol = da.corr(db)
    sol2 = da.corr(db, min_periods=10)
    assert_eq(res, sol)
    assert_eq(res2, sol)
    assert_eq(res3, sol2)
    assert_eq(res4, sol2)
    assert res._name == da.corr(db)._name
    assert res._name != res2._name
    assert res3._name != res4._name
    assert res._name != res3._name

    pytest.raises(NotImplementedError, lambda: da.corr(db, method='spearman'))
    pytest.raises(TypeError, lambda: da.corr(ddf))


def test_cov_corr_meta():
    df = pd.DataFrame({'a': np.array([1, 2, 3]),
                       'b': np.array([1.0, 2.0, 3.0], dtype='f4'),
                       'c': np.array([1.0, 2.0, 3.0])},
                      index=pd.Index([1, 2, 3], name='myindex'))
    ddf = dd.from_pandas(df, npartitions=2)
    assert_eq(ddf.corr(), df.corr())
    assert_eq(ddf.cov(), df.cov())
    assert ddf.a.cov(ddf.b)._meta.dtype == 'f8'
    assert ddf.a.corr(ddf.b)._meta.dtype == 'f8'


@pytest.mark.slow
def test_cov_corr_stable():
    df = pd.DataFrame(np.random.random((20000000, 2)) * 2 - 1, columns=['a', 'b'])
    ddf = dd.from_pandas(df, npartitions=50)
    assert_eq(ddf.cov(split_every=8), df.cov())
    assert_eq(ddf.corr(split_every=8), df.corr())


def test_autocorr():
    x = pd.Series(np.random.random(100))
    dx = dd.from_pandas(x, npartitions=10)
    assert_eq(dx.autocorr(2), x.autocorr(2))
    assert_eq(dx.autocorr(0), x.autocorr(0))
    assert_eq(dx.autocorr(-2), x.autocorr(-2))
    assert_eq(dx.autocorr(2, split_every=3), x.autocorr(2))
    pytest.raises(TypeError, lambda: dx.autocorr(1.5))


def test_apply_infer_columns():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)

    def return_df(x):
        # will create new DataFrame which columns is ['sum', 'mean']
        return pd.Series([x.sum(), x.mean()], index=['sum', 'mean'])

    # DataFrame to completely different DataFrame
    result = ddf.apply(return_df, axis=1)
    assert isinstance(result, dd.DataFrame)
    tm.assert_index_equal(result.columns, pd.Index(['sum', 'mean']))
    assert_eq(result, df.apply(return_df, axis=1))

    # DataFrame to Series
    result = ddf.apply(lambda x: 1, axis=1)
    assert isinstance(result, dd.Series)
    assert result.name is None
    assert_eq(result, df.apply(lambda x: 1, axis=1))

    def return_df2(x):
        return pd.Series([x * 2, x * 3], index=['x2', 'x3'])

    # Series to completely different DataFrame
    result = ddf.x.apply(return_df2)
    assert isinstance(result, dd.DataFrame)
    tm.assert_index_equal(result.columns, pd.Index(['x2', 'x3']))
    assert_eq(result, df.x.apply(return_df2))

    # Series to Series
    result = ddf.x.apply(lambda x: 1)
    assert isinstance(result, dd.Series)
    assert result.name == 'x'
    assert_eq(result, df.x.apply(lambda x: 1))


def test_index_time_properties():
    i = tm.makeTimeSeries()
    a = dd.from_pandas(i, npartitions=3)

    assert (i.index.day == a.index.day.compute()).all()
    assert (i.index.month == a.index.month.compute()).all()


def test_nlargest_nsmallest():
    from string import ascii_lowercase
    df = pd.DataFrame({'a': np.random.permutation(20),
                       'b': list(ascii_lowercase[:20]),
                       'c': np.random.permutation(20).astype('float64')})
    ddf = dd.from_pandas(df, npartitions=3)

    for m in ['nlargest', 'nsmallest']:
        f = lambda df, *args, **kwargs: getattr(df, m)(*args, **kwargs)

        res = f(ddf, 5, 'a')
        res2 = f(ddf, 5, 'a', split_every=2)
        sol = f(df, 5, 'a')
        assert_eq(res, sol)
        assert_eq(res2, sol)
        assert res._name != res2._name

        res = f(ddf, 5, ['a', 'b'])
        res2 = f(ddf, 5, ['a', 'b'], split_every=2)
        sol = f(df, 5, ['a', 'b'])
        assert_eq(res, sol)
        assert_eq(res2, sol)
        assert res._name != res2._name

        res = f(ddf.a, 5)
        res2 = f(ddf.a, 5, split_every=2)
        sol = f(df.a, 5)
        assert_eq(res, sol)
        assert_eq(res2, sol)
        assert res._name != res2._name


def test_reset_index():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)

    sol = df.reset_index()
    res = ddf.reset_index()
    assert all(d is None for d in res.divisions)
    assert_eq(res, sol, check_index=False)

    sol = df.reset_index(drop=True)
    res = ddf.reset_index(drop=True)
    assert all(d is None for d in res.divisions)
    assert_eq(res, sol, check_index=False)

    sol = df.x.reset_index()
    res = ddf.x.reset_index()
    assert all(d is None for d in res.divisions)
    assert_eq(res, sol, check_index=False)

    sol = df.x.reset_index(drop=True)
    res = ddf.x.reset_index(drop=True)
    assert all(d is None for d in res.divisions)
    assert_eq(res, sol, check_index=False)


def test_dataframe_compute_forward_kwargs():
    x = dd.from_pandas(pd.DataFrame({'a': range(10)}), npartitions=2).a.sum()
    x.compute(bogus_keyword=10)


def test_series_iteritems():
    df = pd.DataFrame({'x': [1, 2, 3, 4]})
    ddf = dd.from_pandas(df, npartitions=2)
    for (a, b) in zip(df['x'].iteritems(), ddf['x'].iteritems()):
        assert a == b


def test_dataframe_iterrows():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)

    for (a, b) in zip(df.iterrows(), ddf.iterrows()):
        tm.assert_series_equal(a[1], b[1])


def test_dataframe_itertuples():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)

    for (a, b) in zip(df.itertuples(), ddf.itertuples()):
        assert a == b


def test_from_delayed():
    dfs = [delayed(tm.makeTimeDataFrame)(i) for i in range(1, 5)]
    meta = dfs[0].compute()
    df = dd.from_delayed(dfs, meta=meta)

    assert (df.compute().columns == df.columns).all()
    f = lambda x: pd.Series([len(x)])
    assert list(df.map_partitions(f).compute()) == [1, 2, 3, 4]

    ss = [d.A for d in dfs]
    s = dd.from_delayed(ss, meta=meta.A)

    assert s.compute().name == s.name
    assert list(s.map_partitions(f).compute()) == [1, 2, 3, 4]


def test_from_delayed_sorted():
    a = pd.DataFrame({'x': [1, 2]}, index=[1, 10])
    b = pd.DataFrame({'x': [4, 1]}, index=[100, 200])

    A = dd.from_delayed([delayed(a), delayed(b)], divisions='sorted')
    assert A.known_divisions

    assert A.divisions == (1, 100, 200)


def test_to_delayed():
    from dask.delayed import Delayed
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [10, 20, 30, 40]})
    ddf = dd.from_pandas(df, npartitions=2)
    a, b = ddf.to_delayed()
    assert isinstance(a, Delayed)
    assert isinstance(b, Delayed)

    assert_eq(a.compute(), df.iloc[:2])


def test_astype():
    df = pd.DataFrame({'x': [1, 2, 3, None], 'y': [10, 20, 30, 40]},
                      index=[10, 20, 30, 40])
    a = dd.from_pandas(df, 2)

    assert_eq(a.astype(float), df.astype(float))
    assert_eq(a.x.astype(float), df.x.astype(float))


def test_groupby_callable():
    a = pd.DataFrame({'x': [1, 2, 3, None], 'y': [10, 20, 30, 40]},
                     index=[1, 2, 3, 4])
    b = dd.from_pandas(a, 2)

    def iseven(x):
        return x % 2 == 0

    assert_eq(a.groupby(iseven).y.sum(),
              b.groupby(iseven).y.sum())
    assert_eq(a.y.groupby(iseven).sum(),
              b.y.groupby(iseven).sum())


def test_set_index_sorted_true():
    df = pd.DataFrame({'x': [1, 2, 3, 4],
                       'y': [10, 20, 30, 40],
                       'z': [4, 3, 2, 1]})
    a = dd.from_pandas(df, 2, sort=False)
    assert not a.known_divisions

    b = a.set_index('x', sorted=True)
    assert b.known_divisions
    assert set(a.dask).issubset(set(b.dask))

    for drop in [True, False]:
        assert_eq(a.set_index('x', drop=drop),
                  df.set_index('x', drop=drop))
        assert_eq(a.set_index(a.x, sorted=True, drop=drop),
                  df.set_index(df.x, drop=drop))
        assert_eq(a.set_index(a.x + 1, sorted=True, drop=drop),
                  df.set_index(df.x + 1, drop=drop))

    with pytest.raises(ValueError):
        a.set_index(a.z, sorted=True)


def test_compute_divisions():
    from dask.dataframe.core import compute_divisions
    df = pd.DataFrame({'x': [1, 2, 3, 4],
                       'y': [10, 20, 30, 40],
                       'z': [4, 3, 2, 1]},
                      index=[1, 3, 10, 20])
    a = dd.from_pandas(df, 2, sort=False)
    assert not a.known_divisions

    divisions = compute_divisions(a)
    b = copy(a)
    b.divisions = divisions

    assert_eq(a, b)
    assert b.known_divisions


def test_methods_tokenize_differently():
    df = pd.DataFrame({'x': [1, 2, 3, 4]})
    df = dd.from_pandas(df, npartitions=1)
    assert (df.x.map_partitions(lambda x: pd.Series(x.min()))._name !=
            df.x.map_partitions(lambda x: pd.Series(x.max()))._name)


def test_sorted_index_single_partition():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [1, 0, 1, 0]})
    ddf = dd.from_pandas(df, npartitions=1)
    assert_eq(ddf.set_index('x', sorted=True),
              df.set_index('x'))


def test_info():
    from io import StringIO
    from dask.compatibility import unicode

    # TODO This should be fixed in pandas 0.18.2
    if pd.__version__ == '0.18.0':
        from pandas.core import format
    else:
        from pandas.formats import format
    format._put_lines = put_lines

    test_frames = [
        pd.DataFrame({'x': [1, 2, 3, 4], 'y': [1, 0, 1, 0]}, index=pd.Int64Index(range(4))),  # No RangeIndex in dask
        pd.DataFrame()
    ]

    for df in test_frames:
        buf_pd, buf_da = StringIO(), StringIO()

        ddf = dd.from_pandas(df, npartitions=4)
        df.info(buf=buf_pd)
        ddf.info(buf=buf_da, verbose=True, memory_usage=True)

        stdout_pd = buf_pd.getvalue()
        stdout_da = buf_da.getvalue()
        stdout_da = stdout_da.replace(str(type(ddf)), str(type(df)))

        assert stdout_pd == stdout_da

    buf = StringIO()
    ddf = dd.from_pandas(pd.DataFrame({'x': [1, 2, 3, 4], 'y': [1, 0, 1, 0]}, index=range(4)), npartitions=4)

    # Verbose=False
    ddf.info(buf=buf, verbose=False)
    assert buf.getvalue() == unicode("<class 'dask.dataframe.core.DataFrame'>\n"
                                     "Data columns (total 2 columns):\n"
                                     "x      int64\n"
                                     "y      int64\n"
                                     "dtypes: int64(2)")

    # buf=None
    assert ddf.info(buf=None) is None


def test_categorize_info():
    # assert that we can call info after categorize
    # workaround for: https://github.com/pydata/pandas/issues/14368
    from io import StringIO
    from dask.compatibility import unicode

    # TODO This should be fixed in pandas 0.18.2
    if pd.__version__ == '0.18.0':
        from pandas.core import format
    else:
        from pandas.formats import format
    format._put_lines = put_lines

    df = pd.DataFrame({'x': [1, 2, 3, 4],
                       'y': pd.Series(list('aabc')),
                       'z': pd.Series(list('aabc'))},
                      index=pd.Int64Index(range(4)))  # No RangeIndex in dask
    ddf = dd.from_pandas(df, npartitions=4).categorize(['y'])

    # Verbose=False
    buf = StringIO()
    ddf.info(buf=buf, verbose=True)
    assert buf.getvalue() == unicode("<class 'dask.dataframe.core.DataFrame'>\n"
                                     "Int64Index: 4 entries, 0 to 3\n"
                                     "Data columns (total 3 columns):\n"
                                     "x    4 non-null int64\n"
                                     "y    4 non-null category\n"
                                     "z    4 non-null object\n"
                                     "dtypes: category(1), object(1), int64(1)")


def test_gh_1301():
    df = pd.DataFrame([['1', '2'], ['3', '4']])
    ddf = dd.from_pandas(df, npartitions=2)
    ddf2 = ddf.assign(y=ddf[1].astype(int))
    assert_eq(ddf2, df.assign(y=df[1].astype(int)))

    assert ddf2.dtypes['y'] == np.dtype(int)


def test_timeseries_sorted():
    df = tm.makeTimeDataFrame()
    ddf = dd.from_pandas(df.reset_index(), npartitions=2)
    df.index.name = 'index'
    assert_eq(ddf.set_index('index', sorted=True, drop=True), df)


def test_column_assignment():
    df = pd.DataFrame({'x': [1, 2, 3, 4], 'y': [1, 0, 1, 0]})
    ddf = dd.from_pandas(df, npartitions=2)
    from copy import copy
    orig = copy(ddf)
    ddf['z'] = ddf.x + ddf.y
    df['z'] = df.x + df.y

    assert_eq(df, ddf)
    assert 'z' not in orig.columns


def test_columns_assignment():
    df = pd.DataFrame({'x': [1, 2, 3, 4]})
    ddf = dd.from_pandas(df, npartitions=2)

    df2 = df.assign(y=df.x + 1, z=df.x - 1)
    df[['a', 'b']] = df2[['y', 'z']]

    ddf2 = ddf.assign(y=ddf.x + 1, z=ddf.x - 1)
    ddf[['a', 'b']] = ddf2[['y', 'z']]

    assert_eq(df, ddf)


def test_attribute_assignment():
    df = pd.DataFrame({'x': [1, 2, 3, 4, 5],
                       'y': [1., 2., 3., 4., 5.]})
    ddf = dd.from_pandas(df, npartitions=2)

    ddf.y = ddf.x + ddf.y
    assert_eq(ddf, df.assign(y=df.x + df.y))


def test_inplace_operators():
    df = pd.DataFrame({'x': [1, 2, 3, 4, 5],
                       'y': [1., 2., 3., 4., 5.]})
    ddf = dd.from_pandas(df, npartitions=2)

    ddf.y **= 0.5

    assert_eq(ddf.y, df.y ** 0.5)
    assert_eq(ddf, df.assign(y=df.y ** 0.5))


@pytest.mark.parametrize("skipna", [True, False])
@pytest.mark.parametrize("idx", [
    np.arange(100),
    sorted(np.random.random(size=100)),
    pd.date_range('20150101', periods=100)
])
def test_idxmaxmin(idx, skipna):
    df = pd.DataFrame(np.random.randn(100, 5), columns=list('abcde'), index=idx)
    df.b.iloc[31] = np.nan
    df.d.iloc[78] = np.nan
    ddf = dd.from_pandas(df, npartitions=3)

    assert_eq(df.idxmax(axis=1, skipna=skipna),
              ddf.idxmax(axis=1, skipna=skipna))
    assert_eq(df.idxmin(axis=1, skipna=skipna),
              ddf.idxmin(axis=1, skipna=skipna))

    assert_eq(df.idxmax(skipna=skipna), ddf.idxmax(skipna=skipna))
    assert_eq(df.idxmax(skipna=skipna),
              ddf.idxmax(skipna=skipna, split_every=2))
    assert (ddf.idxmax(skipna=skipna)._name !=
            ddf.idxmax(skipna=skipna, split_every=2)._name)

    assert_eq(df.idxmin(skipna=skipna), ddf.idxmin(skipna=skipna))
    assert_eq(df.idxmin(skipna=skipna),
              ddf.idxmin(skipna=skipna, split_every=2))
    assert (ddf.idxmin(skipna=skipna)._name !=
            ddf.idxmin(skipna=skipna, split_every=2)._name)

    assert_eq(df.a.idxmax(skipna=skipna), ddf.a.idxmax(skipna=skipna))
    assert_eq(df.a.idxmax(skipna=skipna),
              ddf.a.idxmax(skipna=skipna, split_every=2))
    assert (ddf.a.idxmax(skipna=skipna)._name !=
            ddf.a.idxmax(skipna=skipna, split_every=2)._name)

    assert_eq(df.a.idxmin(skipna=skipna), ddf.a.idxmin(skipna=skipna))
    assert_eq(df.a.idxmin(skipna=skipna),
              ddf.a.idxmin(skipna=skipna, split_every=2))
    assert (ddf.a.idxmin(skipna=skipna)._name !=
            ddf.a.idxmin(skipna=skipna, split_every=2)._name)


def test_getitem_meta():
    data = {'col1': ['a', 'a', 'b'],
            'col2': [0, 1, 0]}

    df = pd.DataFrame(data=data, columns=['col1', 'col2'])
    ddf = dd.from_pandas(df, npartitions=1)

    assert_eq(df.col2[df.col1 == 'a'], ddf.col2[ddf.col1 == 'a'])


def test_getitem_multilevel():
    pdf = pd.DataFrame({('A', '0') : [1,2,2], ('B', '1') : [1,2,3]})
    ddf = dd.from_pandas(pdf, npartitions=3)

    assert_eq(pdf['A', '0'], ddf['A', '0'])
    assert_eq(pdf[[('A', '0'), ('B', '1')]], ddf[[('A', '0'), ('B', '1')]])


def test_set_index_sorted_min_max_same():
    a = pd.DataFrame({'x': [1, 2, 3], 'y': [0, 0, 0]})
    b = pd.DataFrame({'x': [1, 2, 3], 'y': [1, 1, 1]})

    aa = delayed(a)
    bb = delayed(b)

    df = dd.from_delayed([aa, bb], meta=a)
    assert not df.known_divisions

    df2 = df.set_index('y', sorted=True)
    assert df2.divisions == (0, 1, 1)


def test_diff():
    df = pd.DataFrame(np.random.randn(100, 5), columns=list('abcde'))
    ddf = dd.from_pandas(df, 5)

    assert_eq(ddf.diff(), df.diff())
    assert_eq(ddf.diff(0), df.diff(0))
    assert_eq(ddf.diff(2), df.diff(2))
    assert_eq(ddf.diff(-2), df.diff(-2))

    assert_eq(ddf.diff(2, axis=1), df.diff(2, axis=1))

    assert_eq(ddf.a.diff(), df.a.diff())
    assert_eq(ddf.a.diff(0), df.a.diff(0))
    assert_eq(ddf.a.diff(2), df.a.diff(2))
    assert_eq(ddf.a.diff(-2), df.a.diff(-2))

    assert ddf.diff(2)._name == ddf.diff(2)._name
    assert ddf.diff(2)._name != ddf.diff(3)._name
    pytest.raises(TypeError, lambda: ddf.diff(1.5))


def test_shift():
    df = tm.makeTimeDataFrame()
    ddf = dd.from_pandas(df, npartitions=4)

    # DataFrame
    assert_eq(ddf.shift(), df.shift())
    assert_eq(ddf.shift(0), df.shift(0))
    assert_eq(ddf.shift(2), df.shift(2))
    assert_eq(ddf.shift(-2), df.shift(-2))

    assert_eq(ddf.shift(2, axis=1), df.shift(2, axis=1))

    # Series
    assert_eq(ddf.A.shift(), df.A.shift())
    assert_eq(ddf.A.shift(0), df.A.shift(0))
    assert_eq(ddf.A.shift(2), df.A.shift(2))
    assert_eq(ddf.A.shift(-2), df.A.shift(-2))

    with pytest.raises(TypeError):
        ddf.shift(1.5)


def test_shift_with_freq():
    df = tm.makeTimeDataFrame(30)
    # DatetimeIndex
    for data_freq, divs1 in [('B', False), ('D', True), ('H', True)]:
        df = df.set_index(tm.makeDateIndex(30, freq=data_freq))
        ddf = dd.from_pandas(df, npartitions=4)
        for freq, divs2 in [('S', True), ('W', False),
                            (pd.Timedelta(10, unit='h'), True)]:
            for d, p in [(ddf, df), (ddf.A, df.A), (ddf.index, df.index)]:
                res = d.shift(2, freq=freq)
                assert_eq(res, p.shift(2, freq=freq))
                assert res.known_divisions == divs2
        # Index shifts also work with freq=None
        res = ddf.index.shift(2)
        assert_eq(res, df.index.shift(2))
        assert res.known_divisions == divs1

    # PeriodIndex
    for data_freq, divs in [('B', False), ('D', True), ('H', True)]:
        df = df.set_index(pd.period_range('2000-01-01', periods=30,
                                          freq=data_freq))
        ddf = dd.from_pandas(df, npartitions=4)
        for d, p in [(ddf, df), (ddf.A, df.A)]:
            res = d.shift(2, freq=data_freq)
            assert_eq(res, p.shift(2, freq=data_freq))
            assert res.known_divisions == divs
        # PeriodIndex.shift doesn't have `freq` parameter
        res = ddf.index.shift(2)
        assert_eq(res, df.index.shift(2))
        assert res.known_divisions == divs

    with pytest.raises(ValueError):
        ddf.index.shift(2, freq='D')  # freq keyword not supported

    # TimedeltaIndex
    for data_freq in ['T', 'D', 'H']:
        df = df.set_index(tm.makeTimedeltaIndex(30, freq=data_freq))
        ddf = dd.from_pandas(df, npartitions=4)
        for freq in ['S', pd.Timedelta(10, unit='h')]:
            for d, p in [(ddf, df), (ddf.A, df.A), (ddf.index, df.index)]:
                res = d.shift(2, freq=freq)
                assert_eq(res, p.shift(2, freq=freq))
                assert res.known_divisions
        # Index shifts also work with freq=None
        res = ddf.index.shift(2)
        assert_eq(res, df.index.shift(2))
        assert res.known_divisions

    # Other index types error
    df = tm.makeDataFrame()
    ddf = dd.from_pandas(df, npartitions=4)
    pytest.raises(NotImplementedError, lambda: ddf.shift(2, freq='S'))
    pytest.raises(NotImplementedError, lambda: ddf.A.shift(2, freq='S'))
    pytest.raises(NotImplementedError, lambda: ddf.index.shift(2))


@pytest.mark.parametrize('method', ['first', 'last'])
def test_first_and_last(method):
    f = lambda x, offset: getattr(x, method)(offset)
    freqs = ['12h', 'D']
    offsets = ['0d', '100h', '20d', '20B', '3W', '3M', '400d', '13M']
    for freq in freqs:
        index = pd.date_range('1/1/2000', '1/1/2001', freq=freq)[::4]
        df = pd.DataFrame(np.random.random((len(index), 4)), index=index,
                          columns=['A', 'B', 'C', 'D'])
        ddf = dd.from_pandas(df, npartitions=10)
        for offset in offsets:
            assert_eq(f(ddf, offset), f(df, offset))
            assert_eq(f(ddf.A, offset), f(df.A, offset))
