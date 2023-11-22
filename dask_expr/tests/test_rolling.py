import pytest

from dask_expr import from_pandas
from dask_expr.tests._util import _backend_library, assert_eq

# Set DataFrame backend for this module
lib = _backend_library()


@pytest.fixture
def pdf():
    idx = lib.date_range("2000-01-01", periods=12, freq="T")
    pdf = lib.DataFrame({"foo": range(len(idx))}, index=idx)
    pdf["bar"] = 1
    yield pdf


@pytest.fixture
def df(pdf, request):
    npartitions = getattr(request, "param", 1)
    yield from_pandas(pdf, npartitions=npartitions)


@pytest.mark.parametrize(
    "api,how_args",
    [
        ("count", ()),
        ("mean", ()),
        ("sum", ()),
        ("min", ()),
        ("max", ()),
        ("var", ()),
        ("std", ()),
        ("median", ()),
        ("skew", ()),
        ("quantile", (0.5,)),
        ("kurt", ()),
    ],
)
@pytest.mark.parametrize("window", (1, 2))
@pytest.mark.parametrize("min_periods", (None, 1))
@pytest.mark.parametrize("center", (True, False))
@pytest.mark.parametrize("df", (1, 2), indirect=True)
def test_rolling_apis(df, pdf, window, api, how_args, min_periods, center):
    args = (window,)
    kwargs = dict(min_periods=min_periods, center=center)

    result = getattr(df.rolling(*args, **kwargs), api)(*how_args)
    expected = getattr(pdf.rolling(*args, **kwargs), api)(*how_args)
    assert_eq(result, expected)

    result = getattr(df.rolling(*args, **kwargs), api)(*how_args)["foo"]
    expected = getattr(pdf.rolling(*args, **kwargs), api)(*how_args)["foo"]
    assert_eq(result, expected)

    q = result.simplify()
    eq = getattr(df["foo"].rolling(*args, **kwargs), api)(*how_args).simplify()
    assert q._name == eq._name


@pytest.mark.parametrize("window", (1, 2))
@pytest.mark.parametrize("df", (1, 2), indirect=True)
def test_resample_agg(df, pdf, window):
    def my_sum(vals, foo=None, *, bar=None):
        return vals.sum()

    result = df.rolling(window).agg(my_sum, "foo", bar="bar")
    expected = pdf.rolling(window).agg(my_sum, "foo", bar="bar")
    assert_eq(result, expected)

    result = df.rolling(window).agg(my_sum)["foo"]
    expected = pdf.rolling(window).agg(my_sum)["foo"]
    assert_eq(result, expected)

    # simplify up disabled for `agg`, function may access other columns
    q = df.rolling(window).agg(my_sum)["foo"].simplify()
    eq = df["foo"].rolling(window).agg(my_sum).simplify()
    assert q._name != eq._name
