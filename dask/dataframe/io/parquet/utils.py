import re
from ....compatibility import string_types


class Engine:
    """ The API necessary to provide a new Parquet reader/writer """

    @staticmethod
    def read_metadata(
        fs, fs_token, paths, categories=None, index=None, gather_statistics=None
    ):
        """ Gather metadata about a Parquet Dataset to prepare for a read

        This function is called once in the user's Python session to gather
        important metadata about the parquet dataset.

        Parameters
        ----------
        fs: FileSystem
        fs_token: ??
        paths; List[str]
            A list of paths to files (or their equivalents)
        categories:
        index: str  # TODO: maybe remove this?
            A suggested column to use as the index, if provided by the user
        gather_statistics: bool
            Whether or not to gather statistics data.  If ``None`` we only
            gather statistics data if there is a single .metadata file to
            cheaply query

        Returns
        -------
        meta: pandas.DataFrame
            An empty DataFrame object to use for metadata.
            Should have appropriate column names and dtypes but need not have
            any actual data
        statistics: Optional[List[Dict]]
            Either none, if no statistics were found, or a list of dictionaries
            of statistics data, one dict for every partition (see the next
            return value).  The statistics should look like the following:

                [
                    {'num-rows': 1000, 'columns': [
                        {'name': 'id', 'min': 0, 'max': 100, 'null-count': 0},
                        {'name': 'value', 'min': 0.0, 'max': 1.0, 'null-count': 5},
                        ]},  # TODO: we might want to rethink this oranization
                    ...
                ]
        parts: List[object]
            A list of objects to be passed to ``Engine.read_partition``.
            Each object should represent a row group of data.
            We don't care about the type of this object, as long as the
            read_partition function knows how to interpret it.
        """
        raise NotImplementedError()

    @staticmethod
    def read_partition(fs, piece, columns, partitions, categories):
        """ Read a single piece of a Parquet dataset into a Pandas DataFrame

        This function is called many times in individual tasks

        Parameters
        ----------
        fs: FileSystem
        piece: object
            This is some token that is returned by Engine.read_metadata.
            Typically it represents a row group in a Parquet dataset
        columns: List[str]
            List of column names to pull out of that row group
        partitions:
        categories:

        Returns
        -------
        A Pandas DataFrame
        """
        raise NotImplementedError()

    @staticmethod
    def write(df, fs, fs_token, path, append=False, partition_on=None, **kwargs):
        """
        Write a Dask DataFrame to Parquet

        Parameters
        ----------
        df: dask.dataframe.DataFrame
        fs: FileSystem
        fs_token:
        path: str
        append: boolean
            Whether or not to append to a previous dataset
        partition_on:
        **kwargs:
            Other keywords as needed by the engine

        Returns
        -------
        out: List[delayed]
            A list of dask.delayed objects, one for each partition
        """
        raise NotImplementedError()


def _parse_pandas_metadata(pandas_metadata):
    """Get the set of names from the pandas metadata section

    Parameters
    ----------
    pandas_metadata : dict
        Should conform to the pandas parquet metadata spec

    Returns
    -------
    index_names : list
        List of strings indicating the actual index names
    column_names : list
        List of strings indicating the actual column names
    storage_name_mapping : dict
        Pairs of storage names (e.g. the field names for
        PyArrow) and actual names. The storage and field names will
        differ for index names for certain writers (pyarrow > 0.8).
    column_indexes_names : list
        The names for ``df.columns.name`` or ``df.columns.names`` for
        a MultiIndex in the columns

    Notes
    -----
    This should support metadata written by at least

    * fastparquet>=0.1.3
    * pyarrow>=0.7.0
    """
    index_storage_names = pandas_metadata["index_columns"]
    index_name_xpr = re.compile(r"__index_level_\d+__")

    # older metadatas will not have a 'field_name' field so we fall back
    # to the 'name' field
    pairs = [
        (x.get("field_name", x["name"]), x["name"]) for x in pandas_metadata["columns"]
    ]

    # Need to reconcile storage and real names. These will differ for
    # pyarrow, which uses __index_leveL_d__ for the storage name of indexes.
    # The real name may be None (e.g. `df.index.name` is None).
    pairs2 = []
    for storage_name, real_name in pairs:
        if real_name and index_name_xpr.match(real_name):
            real_name = None
        pairs2.append((storage_name, real_name))
    index_names = [name for (storage_name, name) in pairs2 if name != storage_name]

    # column_indexes represents df.columns.name
    # It was added to the spec after pandas 0.21.0+, and implemented
    # in PyArrow 0.8. It's not currently impelmented in fastparquet.
    column_index_names = pandas_metadata.get("column_indexes", [{"name": None}])
    column_index_names = [x["name"] for x in column_index_names]

    # Now we need to disambiguate between columns and index names. PyArrow
    # 0.8.0+ allows for duplicates between df.index.names and df.columns
    if not index_names:
        # For PyArrow < 0.8, Any fastparquet. This relies on the facts that
        # 1. Those versions used the real index name as the index storage name
        # 2. Those versions did not allow for duplicate index / column names
        # So we know that if a name is in index_storage_names, it must be an
        # index name
        index_names = list(index_storage_names)  # make a copy
        index_storage_names2 = set(index_storage_names)
        column_names = [
            name for (storage_name, name) in pairs if name not in index_storage_names2
        ]
    else:
        # For newer PyArrows the storage names differ from the index names
        # iff it's an index level. Though this is a fragile assumption for
        # other systems...
        column_names = [name for (storage_name, name) in pairs2 if name == storage_name]

    storage_name_mapping = dict(pairs2)  # TODO: handle duplicates gracefully

    return index_names, column_names, storage_name_mapping, column_index_names


def _normalize_index_columns(user_columns, data_columns, user_index, data_index):
    """Normalize user and file-provided column and index names

    Parameters
    ----------
    user_columns : None, str or list of str
    data_columns : list of str
    user_index : None, str, or list of str
    data_index : list of str

    Returns
    -------
    column_names : list of str
    index_names : list of str
    """
    specified_columns = user_columns is not None
    specified_index = user_index is not None

    if user_columns is None:
        user_columns = list(data_columns)
    elif isinstance(user_columns, string_types):
        user_columns = [user_columns]
    else:
        user_columns = list(user_columns)

    if user_index is None:
        user_index = data_index
    elif user_index is False:
        # When index is False, use no index and all fields should be treated as
        # columns (unless `columns` provided).
        user_index = []
        data_columns = data_index + data_columns
    elif isinstance(user_index, string_types):
        user_index = [user_index]
    else:
        user_index = list(user_index)

    if specified_index and not specified_columns:
        # Only `index` provided. Use specified index, and all column fields
        # that weren't specified as indices
        index_names = user_index
        column_names = [x for x in data_columns if x not in index_names]
    elif specified_columns and not specified_index:
        # Only `columns` provided. Use specified columns, and all index fields
        # that weren't specified as columns
        column_names = user_columns
        index_names = [x for x in data_index if x not in column_names]
    elif specified_index and specified_columns:
        # Both `index` and `columns` provided. Use as specified, but error if
        # they intersect.
        column_names = user_columns
        index_names = user_index
        if set(column_names).intersection(index_names):
            raise ValueError("Specified index and column names must not " "intersect")
    else:
        # Use default columns and index from the metadata
        column_names = data_columns
        index_names = data_index

    return column_names, index_names
