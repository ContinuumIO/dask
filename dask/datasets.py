from __future__ import annotations

import random

from packaging.version import Version

from dask.utils import import_required


def timeseries(
    start="2000-01-01",
    end="2000-01-31",
    freq="1s",
    partition_freq="1d",
    dtypes=None,
    seed=None,
    **kwargs,
):
    """Create timeseries dataframe with random data

    Parameters
    ----------
    start : datetime (or datetime-like string)
        Start of time series
    end : datetime (or datetime-like string)
        End of time series
    dtypes : dict (optional)
        Mapping of column names to types.
        Valid types include {float, int, str, 'category'}
    freq : string
        String like '2s' or '1H' or '12W' for the time series frequency
    partition_freq : string
        String like '1M' or '2Y' to divide the dataframe into partitions
    seed : int (optional)
        Randomstate seed
    kwargs:
        Keywords to pass down to individual column creation functions.
        Keywords should be prefixed by the column name and then an underscore.

    Examples
    --------
    >>> import dask
    >>> df = dask.datasets.timeseries()
    >>> df.head()  # doctest: +SKIP
              timestamp    id     name         x         y
    2000-01-01 00:00:00   967    Jerry -0.031348 -0.040633
    2000-01-01 00:00:01  1066  Michael -0.262136  0.307107
    2000-01-01 00:00:02   988    Wendy -0.526331  0.128641
    2000-01-01 00:00:03  1016   Yvonne  0.620456  0.767270
    2000-01-01 00:00:04   998   Ursula  0.684902 -0.463278
    >>> df = dask.datasets.timeseries(
    ...     '2000', '2010',
    ...     freq='2h', partition_freq='1D', seed=1,  # data frequency
    ...     dtypes={'value': float, 'name': str, 'id': int},  # data types
    ...     id_lam=1000  # control number of items in id column
    ... )
    """
    from dask.dataframe.io.demo import make_timeseries

    if dtypes is None:
        dtypes = {"name": str, "id": int, "x": float, "y": float}

    return make_timeseries(
        start=start,
        end=end,
        freq=freq,
        partition_freq=partition_freq,
        seed=seed,
        dtypes=dtypes,
        **kwargs,
    )


def _generate_mimesis(field, schema_description, records_per_partition, seed):
    """Generate data for a single partition of a dask bag

    See Also
    --------
    _make_mimesis
    """
    import mimesis
    from mimesis.schema import Field, Schema

    field = Field(seed=seed, **field)
    # `iterations=` kwarg moved from `Schema.create()` to `Schema.__init__()`
    # starting with `mimesis=9`.
    schema_kwargs, create_kwargs = {}, {}
    if Version(mimesis.__version__) < Version("9.0.0"):
        create_kwargs["iterations"] = 1
    else:
        schema_kwargs["iterations"] = 1
    schema = Schema(schema=lambda: schema_description(field), **schema_kwargs)
    return [schema.create(**create_kwargs)[0] for i in range(records_per_partition)]


def _make_mimesis(field, schema, npartitions, records_per_partition, seed=None):
    """
    Make a Dask Bag filled with data randomly generated by the mimesis projet

    Parameters
    ----------
    field: dict
        keyword arguments to pass to ``mimesis.Field``
    schema: Callable[Field] -> dict
        The schema to use to generate the data
    npartitions: int
    records_per_partition: int
    seed: int, None
        Seed for random data

    Returns
    -------
    Dask Bag

    See Also
    --------
    make_people
    """
    import dask.bag as db
    from dask.base import tokenize

    field = field or {}

    random_state = random.Random(seed)
    seeds = [random_state.randint(0, 1 << 32) for _ in range(npartitions)]

    name = "mimesis-" + tokenize(
        field, schema, npartitions, records_per_partition, seed
    )
    dsk = {
        (name, i): (_generate_mimesis, field, schema, records_per_partition, seed)
        for i, seed in enumerate(seeds)
    }

    return db.Bag(dsk, name, npartitions)


def make_people(npartitions=10, records_per_partition=1000, seed=None, locale="en"):
    """Make a dataset of random people

    This makes a Dask Bag with dictionary records of randomly generated people.
    This requires the optional library ``mimesis`` to generate records.

    Parameters
    ----------
    npartitions : int
        Number of partitions
    records_per_partition : int
        Number of records in each partition
    seed : int, (optional)
        Random seed
    locale : str
        Language locale, like 'en', 'fr', 'zh', or 'ru'

    Returns
    -------
    b: Dask Bag
    """
    import_required(
        "mimesis",
        "The mimesis module is required for this function.  Try:\n"
        "  python -m pip install mimesis",
    )

    schema = lambda field: {
        "age": field("person.age"),
        "name": (field("person.name"), field("person.surname")),
        "occupation": field("person.occupation"),
        "telephone": field("person.telephone"),
        "address": {"address": field("address.address"), "city": field("address.city")},
        "credit-card": {
            "number": field("payment.credit_card_number"),
            "expiration-date": field("payment.credit_card_expiration_date"),
        },
    }

    return _make_mimesis(
        {"locale": locale}, schema, npartitions, records_per_partition, seed
    )
