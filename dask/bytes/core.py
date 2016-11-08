from __future__ import print_function, division, absolute_import

import io
import os

from toolz import merge
from warnings import warn

from .compression import seekable_files, files as compress_files
from .utils import SeekableFile, read_block
from ..compatibility import PY2, unicode
from ..base import tokenize
from ..delayed import delayed, Delayed, apply
from ..utils import (infer_storage_options, system_encoding,
                     build_name_function, infer_compression,
                     import_required)

# delayed = delayed(pure=True)

# Global registration dictionaries for backend storage functions
# See docstrings to functions below for more information
_read_bytes = dict()
_open_files_write = dict()
_open_files = dict()
_open_text_files = dict()


def write_block_to_file(data, f):
    """
    Parameters
    ----------
    data : data to write
        Either str/bytes, or iterable producing those, or something file-like
        which can be read.
    f : file-like
        backend-dependent file-like object
    """
    binary = 'b' in str(getattr(f, 'mode', 'b'))
    with f as f:
        if isinstance(data, (str, bytes)):
            f.write(data)
        elif isinstance(data, io.IOBase):
            # file-like
            out = '1'
            while out:
                out = data.read(64 * 2 ** 10)
                f.write(out)
        else:
            # iterable, e.g., bag contents
            start = False
            for d in data:
                if start:
                    if binary:
                        try:
                            f.write(b'\n')
                        except TypeError:
                            binary = False
                            f.write('\n')
                    else:
                        f.write('\n')
                else:
                    start = True
                f.write(d)


def write_bytes(data, urlpath, name_function=None, compression=None,
                encoding=None, **kwargs):
    mode = 'wb' if encoding is None else 'wt'
    fs, names, myopen = get_fs_paths_myopen(urlpath, compression, mode,
                   name_function=name_function, num=len(data),
                   encoding=encoding, **kwargs)

    return [delayed(write_block_to_file, pure=False)(d, myopen(f, mode='wb'))
            for d, f in zip(data, names)]


def read_bytes(urlpath, delimiter=None, not_zero=False, blocksize=2**27,
               sample=True, compression=None, **kwargs):
    """ Convert path to a list of delayed values

    The path may be a filename like ``'2015-01-01.csv'`` or a globstring
    like ``'2015-*-*.csv'``.

    The path may be preceded by a protocol, like ``s3://`` or ``hdfs://`` if
    those libraries are installed.

    This cleanly breaks data by a delimiter if given, so that block boundaries
    start directly after a delimiter and end on the delimiter.

    Parameters
    ----------
    urlpath: string
        Absolute or relative filepath, URL (may include protocols like
        ``s3://``), or globstring pointing to data.
    delimiter: bytes
        An optional delimiter, like ``b'\n'`` on which to split blocks of bytes
    not_zero: force seek of start-of-file delimiter, discarding header
    blocksize: int (=128MB)
        Chunk size
    compression: string or None
        String like 'gzip' or 'xz'.  Must support efficient random access.
    sample: bool, int
        Whether or not to return a sample from the first 10k bytes
    **kwargs: dict
        Extra options that make sense to a particular storage connection, e.g.
        host, port, username, password, etc.

    Examples
    --------
    >>> sample, blocks = read_bytes('2015-*-*.csv', delimiter=b'\\n')  # doctest: +SKIP
    >>> sample, blocks = read_bytes('s3://bucket/2015-*-*.csv', delimiter=b'\\n')  # doctest: +SKIP

    Returns
    -------
    10kB sample header and list of ``dask.Delayed`` objects or list of lists of
    delayed objects if ``fn`` is a globstring.
    """
    fs, names, myopen = get_fs_paths_myopen(urlpath, compression, 'rb',
                                            None, **kwargs)
    if len(names) == 0:
        raise IOError("%s resolved to no files" % urlpath)
    sizes = [fs.size(f) for f in names]
    out = []
    # TODO: check fs for preferred locations of blocks here;
    # and preferred blocksize?

    for size, name in zip(sizes, names):
        bs = blocksize if blocksize is not None else size
        offsets = list(range(0, size, bs))
        if len(offsets) > 1 and infer_compression(urlpath):
            raise ValueError('Cannot read compressed files (%s) in byte chunks,'
                             'use blocksize=None' % infer_compression(urlpath))
        if not_zero:
            offsets[0] = 1
        keys = ['read-block-%s-%s' % (offset, tokenize(name,
                compression, offset, kwargs)) for offset in offsets]

        def func(f, off):
            with myopen(f, 'rb') as f:
                return read_block(f, off, bs, delimiter)
        out.append([delayed(func)(name, off, dask_key_name=key)
                    for (off, key) in zip(offsets, keys)])
    if sample is not True:
        nbytes = sample
    else:
        nbytes = 10000
    if sample:
        myopen = MakeMyopen(urlpath, compression)
        with myopen(names[0], 'rb') as f:
            sample = read_block(f, 0, nbytes, delimiter)
    return sample, out


class MakeMyopen(object):
    def __init__(self, urlpath, compression=None, text=False, encoding='utf8',
                 errors=None, **kwargs):
        if compression == 'infer':
            compression = infer_compression(urlpath)
        if compression is not None and compression not in compress_files:
            raise ValueError("Compression type %s not supported" % compression)
        self.urlpath = urlpath
        self.compression = compression
        self.text = text
        self.encoding = encoding
        self.storage_options = infer_storage_options(
                urlpath, inherit_storage_options=kwargs)
        self.protocol = self.storage_options.pop('protocol')
        ensure_protocol(self.protocol)
        try:
            self.fs = _filesystems[self.protocol](**self.storage_options)
        except KeyError:
            raise NotImplementedError("Unknown protocol %s (%s)" %
                                      (protocol, urlpath))

    def __call__(self, path, mode='rb'):
        return OpenFile(self.fs.open, path, self.compression, mode,
                        self.text, self.encoding)


class OpenFile(object):
    def __init__(self, myopen, path, compression, mode, text, encoding):
        self.myopen = myopen
        self.path = path
        self.compression = compression
        self.mode = mode
        self.text = text
        self.encoding = encoding
        self.closers = None
        self.fobjects = None
        self.f = None

    def __enter__(self):
        mode = self.mode.replace('t', '').replace('b', '') + 'b'
        f = f2 = self.myopen(self.path, mode=mode)
        CompressFile = merge(seekable_files, compress_files)[self.compression]
        if PY2:
            f2 = SeekableFile(f)
        f3 = CompressFile(f2, mode=mode)
        if self.text:
            f4 = io.TextIOWrapper(f3, encoding=self.encoding)
        else:
            f4 = f3

        self.closers = [f4.close, f3.close, f2.close, f.close]
        self.fobjects = [f4, f3, f2, f]
        self.f = f4
        f4.close = self.close
        return f4

    def __exit__(self, *args):
        self.close()

    def close(self):
        [_() for _ in self.closers]


def open_files(urlpath, compression=None, mode='rb', encoding='utf8',
               errors=None, name_function=None, num=1, **kwargs):
    """ Given path return dask.delayed file-like objects

    Parameters
    ----------
    urlpath: string
        Absolute or relative filepath, URL (may include protocols like
        ``s3://``), or globstring pointing to data.
    compression: string
        Compression to use.  See ``dask.bytes.compression.files`` for options.
    mode: 'rb', 'wt', etc.
    encoding: str
        For text mode only
    errors: None or str
        Passed to TextIOWrapper in text mode
    name_function: function or None
        if opening a set of files for writing, those files do not yet exist,
        so we need to generate their names by formatting the urlpath for
        each sequence number
    num: int [1]
        if writing mode, number of files we expect to create (passed to
        name+function)
    **kwargs: dict
        Extra options that make sense to a particular storage connection, e.g.
        host, port, username, password, etc.

    Examples
    --------
    >>> files = open_files('2015-*-*.csv')  # doctest: +SKIP
    >>> files = open_files('s3://bucket/2015-*-*.csv.gz', compression='gzip')  # doctest: +SKIP

    Returns
    -------
    List of ``dask.delayed`` objects that compute to file-like objects
    """
    fs, paths, myopen = get_fs_paths_myopen(urlpath, compression, mode,
                                            encoding=encoding, num=num,
                                            name_function=name_function,
                                            **kwargs)
    return [myopen(path, mode) for path in paths]


def get_fs_paths_myopen(urlpath, compression, mode, encoding='utf8',
                        num=1, name_function=None, **kwargs):
    if isinstance(urlpath, (str, unicode)):
        myopen = MakeMyopen(urlpath, compression, text='b' not in mode,
                            encoding=encoding, **kwargs)
        if 'w' in mode:
            paths = _expand_paths(urlpath, name_function, num)
        elif "*" in urlpath:
            paths = myopen.fs.glob(urlpath, **kwargs)
        else:
            paths = [urlpath]
    elif isinstance(urlpath, (list, set, tuple, dict)):
        myopen = MakeMyopen(urlpath[0], compression, text='b' not in mode,
                            encoding='utf8', **kwargs)
        paths = urlpath
    else:
        raise ValueError('url type not understood: %s' % urlpath)
    return myopen.fs, paths, myopen


def open_text_files(urlpath, compression=None, mode='rb', encoding='utf8',
                    errors='strict', **kwargs):
    """ Given path return dask.delayed file-like objects in text mode

    Parameters
    ----------
    urlpath: string
        Absolute or relative filepath, URL (may include protocols like
        ``s3://``), or globstring pointing to data.
    encoding: string
    errors: string
    compression: string
        Compression to use.  See ``dask.bytes.compression.files`` for options.
    **kwargs: dict
        Extra options that make sense to a particular storage connection, e.g.
        host, port, username, password, etc.

    Examples
    --------
    >>> files = open_text_files('2015-*-*.csv', encoding='utf-8')  # doctest: +SKIP
    >>> files = open_text_files('s3://bucket/2015-*-*.csv')  # doctest: +SKIP

    Returns
    -------
    List of ``dask.delayed`` objects that compute to text file-like objects
    """
    return open_files(urlpath, compression, mode.replace('b', 't'), encoding,
                      **kwargs)


def _expand_paths(path, name_function, num):
    if isinstance(path, (str, unicode)):
        if path.count('*') > 1:
            raise ValueError("Output path spec must contain at most one '*'.")
        if name_function is None:
            name_function = build_name_function(num - 1)

        if '*' not in path:
            path = os.path.join(path, '*.part')

        formatted_names = [name_function(i) for i in range(num)]
        if formatted_names != sorted(formatted_names):
            warn("In order to preserve order between partitions "
                 "name_function must preserve the order of its input")

        paths = [path.replace('*', name_function(i))
                 for i in range(num)]
    elif isinstance(path, (tuple, list, set)):
        assert len(path) == num
        paths = path
    else:
        raise ValueError("""Path should be either"
1.  A list of paths -- ['foo.json', 'bar.json', ...]
2.  A directory -- 'foo/
3.  A path with a * in it -- 'foo.*.json'""")
    print(path, paths)
    return paths


def ensure_protocol(protocol):
    if (protocol not in ('s3', 'hdfs') and ((protocol in _read_bytes) or
       (protocol in _open_files))):
        return

    if protocol == 's3':
        import_required('s3fs',
                        "Need to install `s3fs` library for s3 support\n"
                        "    conda install s3fs -c conda-forge\n"
                        "    or\n"
                        "    pip install s3fs")

    elif protocol == 'hdfs':
        msg = ("Need to install `distributed` and `hdfs3` "
               "for HDFS support\n"
               "    conda install distributed hdfs3 -c conda-forge")
        import_required('distributed.hdfs', msg)
        import_required('hdfs3', msg)

    else:
        raise ValueError("Unknown protocol %s" % protocol)


_filesystems = dict()
# see .local.LocalFileSystem for reference implementation

