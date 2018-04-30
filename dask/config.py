from __future__ import print_function, division, absolute_import

import ast
from contextlib import contextmanager
import copy
import os
import sys


no_default = '__no_default__'


config_paths = [
    '/etc/dask',
    os.path.join(sys.prefix, 'etc', 'dask'),
    os.path.join(os.path.expanduser('~'), '.config', 'dask'),
    os.path.join(os.path.expanduser('~'), '.dask')
]

if 'DASK_CONFIG' in os.environ:
    config_paths.append(os.environ['DASK_CONFIG'])


def update(old, new, priority='new'):
    """ Update a nested dictionary with values from another

    This is like dict.update except that it smoothly merges nested values

    This operates in-place and modifies old

    Parameters
    ----------
    priority: string {'old', 'new'}
        If new (default) then the new dictionary has preference.
        Otherwise the old dictionary does.

    Example
    -------
    >>> a = {'x': 1, 'y': {'a': 2}}
    >>> b = {'x': 2, 'y': {'b': 3}}
    >>> update(a, b)  # doctest: +SKIP
    {'x': 2, 'y': {'a': 2, 'b': 3}}

    >>> a = {'x': 1, 'y': {'a': 2}}
    >>> b = {'x': 2, 'y': {'b': 3}}
    >>> update(a, b, priority='old')  # doctest: +SKIP
    {'x': 1, 'y': {'a': 2, 'b': 3}}

    See Also
    --------
    merge
    """
    for k, v in new.items():
        if k not in old and type(v) is dict:
            old[k] = {}

        if type(v) is dict:
            update(old[k], v, priority=priority)
        else:
            if priority == 'new' or k not in old:
                old[k] = v

    return old


def merge(*dicts):
    """ Update a sequence of nested dictionaries

    This prefers the values in the latter dictionaries to those in the former

    Example
    -------
    >>> a = {'x': 1, 'y': {'a': 2}}
    >>> b = {'y': {'b': 3}}
    >>> merge(a, b)  # doctest: +SKIP
    {'x': 1, 'y': {'a': 2, 'b': 3}}

    See Also
    --------
    update
    """
    result = {}
    for d in dicts:
        update(result, d)
    return result


def collect_yaml(paths=config_paths):
    """ Collect configuration from yaml files

    This searches through ``config_paths``, expands to find all yaml or json
    files, and then parses each file.
    """
    # Find all paths
    file_paths = []
    for path in paths:
        if os.path.exists(path):
            if os.path.isdir(path):
                file_paths.extend(sorted([
                    p for p in os.listdir(path)
                    if os.path.splitext(p)[1].lower() in ('json', 'yaml', 'yml')
                ]))
            else:
                file_paths.append(path)

    configs = []

    # Parse yaml files
    for path in file_paths:
        with open(path) as f:
            data = yaml.load(f.read()) or {}
            configs.append(data)

    return configs


def collect_env():
    """ Collect config from environment variables

    This grabs environment variables of the form "DASK_FOO_BAR=123"
    and turns these into config variables of the form ``{"foo-bar": 123}``
    """
    env = {}
    for name, value in os.environ.items():
        if name.startswith('DASK_'):
            varname = name[5:].lower().replace('_', '-')
            try:
                env[varname] = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                env[varname] = value

    return env


def ensure_config_file(
        source,
        destination=os.path.join(os.path.expanduser('~'), '.config', 'dask'),
        comment=True):
    """ Copy file to default location if it does not already exist

    This tries to move a default configuration file to a default location if
    if does not already exist.  It also comments out that file by default.

    This is to be used by downstream modules (like dask.distributed) that may
    have default configuration files that they wish to include in the default
    configuration path.

    Parameters
    ----------
    source: string, filename
        source configuration file, typically within a source directory
    destination: string, filename
        destination filename, typically ~/.config/dask
    comment: bool, True by default
        Whether or not to comment out the config file when copying
    """
    if not os.path.exists(os.path.dirname(destination)):
        os.makedirs(os.path.dirname(destination), exists_ok=True)

    if os.path.isdir(destination):
        _, filename = os.path.split(source)
        destination = os.path.join(destination, filename)

    if not os.path.exists(destination):
        # Atomically create destination.  Parallel testing discovered
        # a race condition where a process can be busy creating the
        # destination while another process reads an empty config file.
        tmp = '%s.tmp.%d' % (destination, os.getpid())
        with open(source) as f:
            lines = list(f)

        if comment:
            lines = ['#' + line if line else line for line in lines]

        with open(tmp, 'w') as f:
            f.write(os.linesep.join(lines))

        try:
            os.rename(tmp, destination)
        except OSError:
            os.remove(tmp)


configs = []

try:
    import yaml
except ImportError:
    pass
else:
    configs.extend(collect_yaml())

configs.append(collect_env())

config = merge(*configs)


def get(key, default=no_default, config=config):
    """
    Get elements from global config

    Use '.' for nested access

    Examples
    --------
    >>> from dask import config
    >>> config.get('foo')  # doctest: +SKIP
    {'x': 1, 'y': 2}

    >>> config.get('foo.x')  # doctest: +SKIP
    1

    >>> config.get('foo.x.y', default=123)  # doctest: +SKIP
    123
    """
    keys = key.split('.')
    result = config
    for k in keys:
        try:
            result = result[k]
        except (TypeError, IndexError, KeyError):
            if default is not no_default:
                return default
            else:
                raise
    return result


@contextmanager
def set_config(arg=None, config=config, **kwargs):
    """ Temporarily set configuration values within a context manager

    Examples
    --------
    >>> with set_config({'foo': 123}):
    ...     pass
    """
    if arg and not kwargs:
        kwargs = arg

    old = copy.deepcopy(config)

    def assign(keys, value, d):
        key = keys[0]
        if len(keys) == 1:
            d[keys[0]] = value
        else:
            if key not in d:
                d[key] = {}
            assign(keys[1:], value, d[key])

    for key, value in kwargs.items():
        assign(key.split('.'), value, config)

    try:
        yield
    finally:
        config.clear()
        config.update(old)
