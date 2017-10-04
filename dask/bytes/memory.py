from __future__ import print_function, division, absolute_import

from glob import glob
from io import BytesIO
import os
import re
import time

from . import core
from .utils import infer_storage_options
from ..base import tokenize
from ..compatibility import FileNotFoundError


class MemoryFileSystem(core.FileSystem):
    """A filesystem based on a dict of BytesIO objects"""
    sep = '/'
    store = {}

    def __init__(self, **storage_options):
        """
        Parameters
        ----------
        storage_options: key-value
            May be credentials, or other configuration specific to the backend.
        """
        pass

    def _trim_filename(self, fn):
        if fn.startswith('memory://'):
            fn = fn[9:]
        return fn

    def glob(self, path):
        """For a template path, return matching files"""
        path = self._trim_filename(path)
        pattern = re.compile("^" + path.replace('//', '/')
                             .rstrip('/')
                             .replace('*', '[^/]*')
                             .replace('?', '.') + "$")
        files = [f for f in self.store if pattern.match(f)]
        return sorted(files)

    def mkdirs(self, path):
        """Make any intermediate directories to make path writable"""
        pass

    def open(self, path, mode='rb', **kwargs):
        """Make a file-like object

        Parameters
        ----------
        path: str
            identifier
        mode: string
            normally "rb", "wb" or "ab" or other.
        """
        path = self._trim_filename(path)
        if 'b' not in mode:
            raise ValueError('Only bytes mode allowed')
        if mode in ['rb', 'ab', 'rb+']:
            if path in self.store:
                f = self.store[path]
                if mode == 'rb':
                    f.seek(0)
                else:
                    f.seek(0, 2)
                return f
            else:
                raise FileNotFoundError(path)
        if mode == 'wb':
            self.store[path] = MemoryFile()
            return self.store[path]

    def ukey(self, path):
        """Unique identifier, so we can tell if a file changed"""
        path = self._trim_filename(path)
        if path not in self.store:
            raise FileNotFoundError(path)
        return time.time()

    def size(self, path):
        """Size in bytes of the file at path"""
        path = self._trim_filename(path)
        if path not in self.store:
            raise FileNotFoundError(path)
        return len(self.store[path].getbuffer())


core._filesystems['memory'] = MemoryFileSystem


class MemoryFile(BytesIO):
    """A BytesIO which can't close and works as a context manager"""

    def __enter__(self):
        return self

    def close(self):
        pass
