from __future__ import annotations

import abc
import copy
import html
from collections.abc import Hashable, Iterable, Mapping, MutableMapping, Set
from typing import Any

import tlz as toolz

from . import config
from .base import clone_key, flatten, is_dask_collection
from .core import keys_in_tasks, reverse_dict
from .utils import ensure_dict, key_split, stringify
from .utils_test import add, inc  # noqa: F401
from .widgets import get_template


def compute_layer_dependencies(layers):
    """Returns the dependencies between layers"""

    def _find_layer_containing_key(key):
        for k, v in layers.items():
            if key in v:
                return k
        raise RuntimeError(f"{repr(key)} not found")

    all_keys = {key for layer in layers.values() for key in layer}
    ret = {k: set() for k in layers}
    for k, v in layers.items():
        for key in keys_in_tasks(all_keys - v.keys(), v.values()):
            ret[k].add(_find_layer_containing_key(key))
    return ret


class Layer(Mapping):
    """High level graph layer

    This abstract class establish a protocol for high level graph layers.

    The main motivation of a layer is to represent a collection of tasks
    symbolically in order to speedup a series of operations significantly.
    Ideally, a layer should stay in this symbolic state until execution
    but in practice some operations will force the layer to generate all
    its internal tasks. We say that the layer has been materialized.

    Most of the default implementations in this class will materialize the
    layer. It is up to derived classes to implement non-materializing
    implementations.
    """

    annotations: Mapping[str, Any] | None
    collection_annotations: Mapping[str, Any] | None

    def __init__(
        self,
        annotations: Mapping[str, Any] = None,
        collection_annotations: Mapping[str, Any] = None,
    ):
        """Initialize Layer object.

        Parameters
        ----------
        annotations : Mapping[str, Any], optional
            By default, None.
            Annotations are metadata or soft constraints associated with tasks
            that dask schedulers may choose to respect:
            They signal intent without enforcing hard constraints.
            As such, they are primarily designed for use with the distributed
            scheduler. See the dask.annotate function for more information.
        collection_annotations : Mapping[str, Any], optional. By default, None.
            Experimental, intended to assist with visualizing the performance
            characteristics of Dask computations.
            These annotations are *not* passed to the distributed scheduler.
        """
        self.annotations = annotations or copy.copy(config.get("annotations", None))
        self.collection_annotations = collection_annotations or copy.copy(
            config.get("collection_annotations", None)
        )

    @abc.abstractmethod
    def is_materialized(self) -> bool:
        """Return whether the layer is materialized or not"""
        return True

    @abc.abstractmethod
    def get_output_keys(self) -> Set:
        """Return a set of all output keys

        Output keys are all keys in the layer that might be referenced by
        other layers.

        Classes overriding this implementation should not cause the layer
        to be materialized.

        Returns
        -------
        keys: Set
            All output keys
        """
        return self.keys()  # this implementation will materialize the graph

    def cull(
        self, keys: set, all_hlg_keys: Iterable
    ) -> tuple[Layer, Mapping[Hashable, set]]:
        """Remove unnecessary tasks from the layer

        In other words, return a new Layer with only the tasks required to
        calculate `keys` and a map of external key dependencies.

        Examples
        --------
        >>> d = MaterializedLayer({'x': 1, 'y': (inc, 'x'), 'out': (add, 'x', 10)})
        >>> _, deps = d.cull({'out'}, d.keys())
        >>> deps
        {'out': {'x'}, 'x': set()}

        Returns
        -------
        layer: Layer
            Culled layer
        deps: Map
            Map of external key dependencies
        """

        if len(keys) == len(self):
            # Nothing to cull if preserving all existing keys
            return (
                self,
                {k: self.get_dependencies(k, all_hlg_keys) for k in self.keys()},
            )

        ret_deps = {}
        seen = set()
        out = {}
        work = keys.copy()
        while work:
            k = work.pop()
            out[k] = self[k]
            ret_deps[k] = self.get_dependencies(k, all_hlg_keys)
            for d in ret_deps[k]:
                if d not in seen:
                    if d in self:
                        seen.add(d)
                        work.add(d)

        return MaterializedLayer(out, annotations=self.annotations), ret_deps

    def get_dependencies(self, key: Hashable, all_hlg_keys: Iterable) -> set:
        """Get dependencies of `key` in the layer

        Parameters
        ----------
        key: Hashable
            The key to find dependencies of
        all_hlg_keys: Iterable
            All keys in the high level graph.

        Returns
        -------
        deps: set
            A set of dependencies
        """
        return keys_in_tasks(all_hlg_keys, [self[key]])

    def __dask_distributed_annotations_pack__(
        self, annotations: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any] | None:
        """Packs Layer annotations for transmission to scheduler

        Callables annotations are fully expanded over Layer keys, while
        other values are simply transmitted as is

        Parameters
        ----------
        annotations : Mapping[str, Any], optional
            A top-level annotations.

        Returns
        -------
        packed_annotations : dict
            Packed annotations.
        """
        annotations = toolz.merge(self.annotations or {}, annotations or {})
        packed = {}
        for a, v in annotations.items():
            if callable(v):
                packed[a] = {stringify(k): v(k) for k in self}
                packed[a]["__expanded_annotations__"] = True
            else:
                packed[a] = v
        return packed

    @staticmethod
    def __dask_distributed_annotations_unpack__(
        annotations: MutableMapping[str, Any],
        new_annotations: Mapping[str, Any] | None,
        keys: Iterable[Hashable],
    ) -> None:
        """
        Unpack a set of layer annotations across a set of keys, then merge those
        expanded annotations for the layer into an existing annotations mapping.

        This is not a simple shallow merge because some annotations like retries,
        priority, workers, etc need to be able to retain keys from different layers.

        Parameters
        ----------
        annotations: MutableMapping[str, Any], input/output
            Already unpacked annotations, which are to be updated with the new
            unpacked annotations
        new_annotations: Mapping[str, Any], optional
            New annotations to be unpacked into `annotations`
        keys: Iterable
            All keys in the layer.
        """
        if new_annotations is None:
            return

        expanded = {}
        keys_stringified = False

        # Expand the new annotations across the keyset
        for a, v in new_annotations.items():
            if type(v) is dict and "__expanded_annotations__" in v:
                # Maybe do a destructive update for efficiency?
                v = v.copy()
                del v["__expanded_annotations__"]
                expanded[a] = v
            else:
                if not keys_stringified:
                    keys = [stringify(k) for k in keys]
                    keys_stringified = True

                expanded[a] = dict.fromkeys(keys, v)

        # Merge the expanded annotations with the existing annotations mapping
        for k, v in expanded.items():
            v.update(annotations.get(k, {}))
        annotations.update(expanded)

    def clone(
        self,
        keys: set,
        seed: Hashable,
        bind_to: Hashable = None,
    ) -> tuple[Layer, bool]:
        """Clone selected keys in the layer, as well as references to keys in other
        layers

        Parameters
        ----------
        keys
            Keys to be replaced. This never includes keys not listed by
            :meth:`get_output_keys`. It must also include any keys that are outside
            of this layer that may be referenced by it.
        seed
            Common hashable used to alter the keys; see :func:`dask.base.clone_key`
        bind_to
            Optional key to bind the leaf nodes to. A leaf node here is one that does
            not reference any replaced keys; in other words it's a node where the
            replacement graph traversal stops; it may still have dependencies on
            non-replaced nodes.
            A bound node will not be computed until after ``bind_to`` has been computed.

        Returns
        -------
        - New layer
        - True if the ``bind_to`` key was injected anywhere; False otherwise

        Notes
        -----
        This method should be overridden by subclasses to avoid materializing the layer.
        """
        from .graph_manipulation import chunks

        is_leaf: bool

        def clone_value(o):
            """Variant of distributed.utils_comm.subs_multiple, which allows injecting
            bind_to
            """
            nonlocal is_leaf

            typ = type(o)
            if typ is tuple and o and callable(o[0]):
                return (o[0],) + tuple(clone_value(i) for i in o[1:])
            elif typ is list:
                return [clone_value(i) for i in o]
            elif typ is dict:
                return {k: clone_value(v) for k, v in o.items()}
            else:
                try:
                    if o not in keys:
                        return o
                except TypeError:
                    return o
                is_leaf = False
                return clone_key(o, seed)

        dsk_new = {}
        bound = False

        for key, value in self.items():
            if key in keys:
                key = clone_key(key, seed)
                is_leaf = True
                value = clone_value(value)
                if bind_to is not None and is_leaf:
                    value = (chunks.bind, value, bind_to)
                    bound = True

            dsk_new[key] = value

        return MaterializedLayer(dsk_new), bound

    def __dask_distributed_pack__(
        self,
        all_hlg_keys: Iterable[Hashable],
        known_key_dependencies: Mapping[Hashable, set],
        client,
        client_keys: Iterable[Hashable],
    ) -> Any:
        """Pack the layer for scheduler communication in Distributed

        This method should pack its current state and is called by the Client when
        communicating with the Scheduler.
        The Scheduler will then use .__dask_distributed_unpack__(data, ...) to unpack
        the state, materialize the layer, and merge it into the global task graph.

        The returned state must be compatible with Distributed's scheduler, which
        means it must obey the following:
          - Serializable by msgpack (notice, msgpack converts lists to tuples)
          - All remote data must be unpacked (see unpack_remotedata())
          - All keys must be converted to strings now or when unpacking
          - All tasks must be serialized (see dumps_task())

        The default implementation materialize the layer thus layers such as Blockwise
        and ShuffleLayer should implement a specialized pack and unpack function in
        order to avoid materialization.

        Parameters
        ----------
        all_hlg_keys: Iterable[Hashable]
            All keys in the high level graph
        known_key_dependencies: Mapping[Hashable, set]
            Already known dependencies
        client: distributed.Client
            The client calling this function.
        client_keys : Iterable[Hashable]
            List of keys requested by the client.

        Returns
        -------
        state: Object serializable by msgpack
            Scheduler compatible state of the layer
        """
        from distributed.client import Future
        from distributed.utils import CancelledError
        from distributed.utils_comm import subs_multiple, unpack_remotedata
        from distributed.worker import dumps_task

        dsk = dict(self)

        # Find aliases not in `client_keys` and substitute all matching keys
        # with its Future
        future_aliases = {
            k: v
            for k, v in dsk.items()
            if isinstance(v, Future) and k not in client_keys
        }
        if future_aliases:
            dsk = subs_multiple(dsk, future_aliases)

        # Remove `Future` objects from graph and note any future dependencies
        dsk2 = {}
        fut_deps = {}
        for k, v in dsk.items():
            dsk2[k], futs = unpack_remotedata(v, byte_keys=True)
            if futs:
                fut_deps[k] = futs
        dsk = dsk2

        # Check that any collected futures are valid
        unpacked_futures = set.union(*fut_deps.values()) if fut_deps else set()
        for future in unpacked_futures:
            if future.client is not client:
                raise ValueError(
                    "Inputs contain futures that were created by another client."
                )
            if stringify(future.key) not in client.futures:
                raise CancelledError(stringify(future.key))

        # Calculate dependencies without re-calculating already known dependencies
        # - Start with known dependencies
        dependencies = known_key_dependencies.copy()
        # - Remove aliases for any tasks that depend on both an alias and a future.
        #   These can only be found in the known_key_dependencies cache, since
        #   any dependencies computed in this method would have already had the
        #   aliases removed.
        if future_aliases:
            alias_keys = set(future_aliases)
            dependencies = {k: v - alias_keys for k, v in dependencies.items()}
        # - Add in deps for any missing keys
        missing_keys = dsk.keys() - dependencies.keys()
        dependencies.update(
            (k, keys_in_tasks(all_hlg_keys, [dsk[k]], as_list=False))
            for k in missing_keys
        )
        # - Add in deps for any tasks that depend on futures
        for k, futures in fut_deps.items():
            dependencies[k].update(f.key for f in futures)

        # The scheduler expect all keys to be strings
        dependencies = {
            stringify(k): {stringify(dep) for dep in deps}
            for k, deps in dependencies.items()
        }

        merged_hlg_keys = all_hlg_keys | dsk.keys()
        dsk = {
            stringify(k): stringify(v, exclusive=merged_hlg_keys)
            for k, v in dsk.items()
        }
        dsk = toolz.valmap(dumps_task, dsk)
        return {"dsk": dsk, "dependencies": dependencies}

    @classmethod
    def __dask_distributed_unpack__(
        cls,
        state: Any,
        dsk: Mapping[str, Any],
        dependencies: Mapping[str, set],
    ) -> dict:
        """Unpack the state of a layer previously packed by __dask_distributed_pack__()

        This method is called by the scheduler in Distributed in order to unpack
        the state of a layer and merge it into its global task graph. The method
        can use `dsk` and `dependencies`, which are the already materialized
        state of the preceding layers in the high level graph. The layers of the
        high level graph are unpacked in topological order.

        See Layer.__dask_distributed_pack__() for packing detail.

        Parameters
        ----------
        state: Any
            The state returned by Layer.__dask_distributed_pack__()
        dsk: Mapping, read-only
            The materialized low level graph of the already unpacked layers
        dependencies: Mapping, read-only
            The dependencies of each key in `dsk`

        Returns
        -------
        unpacked-layer: dict
            layer_dsk: Mapping[str, Any]
                Materialized (stringified) graph of the layer
            layer_deps: Mapping[str, set]
                Dependencies of each key in `layer_dsk`
        """
        return {"dsk": state["dsk"], "deps": state["dependencies"]}

    def __reduce__(self):
        """Default serialization implementation, which materializes the Layer"""
        return (MaterializedLayer, (dict(self),))

    def __copy__(self):
        """Default shallow copy implementation"""
        obj = type(self).__new__(self.__class__)
        obj.__dict__.update(self.__dict__)
        return obj

    def _repr_html_(self, layer_index="", highlevelgraph_key=""):
        if highlevelgraph_key != "":
            shortname = key_split(highlevelgraph_key)
        elif hasattr(self, "name"):
            shortname = key_split(self.name)
        else:
            shortname = self.__class__.__name__

        svg_repr = ""
        if (
            self.collection_annotations
            and self.collection_annotations.get("type") == "dask.array.core.Array"
        ):
            chunks = self.collection_annotations.get("chunks")
            if chunks:
                from .array.svg import svg

                svg_repr = svg(chunks)

        return get_template("highlevelgraph_layer.html.j2").render(
            materialized=self.is_materialized(),
            shortname=shortname,
            layer_index=layer_index,
            highlevelgraph_key=highlevelgraph_key,
            info=self.layer_info_dict(),
            svg_repr=svg_repr,
        )

    def layer_info_dict(self):
        info = {
            "layer_type": type(self).__name__,
            "is_materialized": self.is_materialized(),
        }
        if self.annotations is not None:
            for key, val in self.annotations.items():
                info[key] = html.escape(str(val))
        if self.collection_annotations is not None:
            for key, val in self.collection_annotations.items():
                # Hide verbose chunk details from the HTML table
                if key != "chunks":
                    info[key] = html.escape(str(val))
        return info


class AbstractLayer(Layer):
    """HighLevelGraph-Layer Template

    This abstract class is intended to isolate the typical
    boilerplate code required for HLG-Layer implementation.
    A typical inheriting class will only need to define
    ``__init__``, ``layer_state``, ``layer_dependencies``,
    and ``construct_graph``.

    Parameters
    ----------
    output_blocks: List; Optional
        The list of output indices required in the graph
        after materialization. Culling an ``AbstractLayer``
        is equivalent to setting this attribute.
    """

    annotations: Mapping[str, Any] | None
    output_blocks: list[int] | None

    def __init__(
        self,
        *args,
        annotations: Mapping[str, Any] | None = None,
        output_blocks: list[int] | None = None,
        **kwargs,
    ):
        # NOTE: `AbstractLayer` assumes that an `output_blocks`
        # attribute will be used for culling
        super().__init__(annotations=annotations)
        self.output_blocks = output_blocks

    @classmethod
    def reconstructor(cls):
        """Specify the specific class to use with
        ``layer_state`` for culling and materialization
        on the scheduler. Some Layers may need to down-
        cast after collection-sepcific optimizations
        (e.g. ``DataFrameIOLayer``). However, the default
        is to use the current class.
        """
        return cls

    @property
    def layer_state(self):
        """Dictionary of key-word arguments required to
        to recreate this `AbstractLayer` on the scheduler.
        These arguments must include the necessary state
        to materialize a valid graph on the scheduler.
        """
        raise NotImplementedError

    def layer_dependencies(self, keys, output_blocks=None):
        """Determine the necessary dependencies to produce `keys`"""
        raise NotImplementedError

    def construct_graph(self):
        """Materialize a low-level (dictionary) task graph

        This method should check the ``output_blocks`` attribute,
        and only materialize the necessary low-level graph to
        produce keys corresponding to these collection indices.
        """
        raise NotImplementedError

    def cull(self, keys, all_keys):
        """Cull an AbstractLayer

        The underlying graph will only include the necessary
        tasks to produce the keys (indicies) included in `output_blocks`.
        Therefore, "culling" the layer should only require us to reset
        this parameter.
        """
        output_blocks = self._keys_to_indices(keys)
        if output_blocks != set(self.output_blocks):
            new_state = self.layer_state.copy()
            new_state["output_blocks"] = output_blocks
            new_state["annotations"] = self.annotations
            culled_layer = self.reconstructor()(**new_state)
        else:
            culled_layer = self
        return culled_layer, culled_layer.layer_dependencies(
            keys, output_blocks=output_blocks
        )

    def is_materialized(self):
        return hasattr(self, "_cached_dict")

    @property
    def _dict(self):
        """Materialize full dict representation"""
        if hasattr(self, "_cached_dict"):
            return self._cached_dict
        else:
            dsk = self.construct_graph()
            self._cached_dict = dsk
        return self._cached_dict

    def get_output_keys(self):
        if hasattr(self, "_cached_output_keys"):
            return self._cached_output_keys
        else:
            if self.output_blocks is None:
                output_keys = self.keys()  # Materializes the graph!
            else:
                output_keys = {(self.name, block) for block in self.output_blocks}
            self._cached_output_keys = output_keys
        return self._cached_output_keys

    def _keys_to_indices(self, keys):
        """Convert keys to output chunk/partition indices

        Currently works for keys of the form: `(name, index)`.
        """
        parts = set()
        for key in keys:
            try:
                _name, _part = key
            except ValueError:
                continue
            if _name != self.name:
                continue
            parts.add(_part)
        return parts

    def __getitem__(self, key):
        return self._dict[key]

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __reduce__(self):
        attrs = list(self.layer_state.keys()) + ["annotations"]
        return (self.__class__, tuple(getattr(self, attr) for attr in attrs))

    def __dask_distributed_pack__(self, all_hlg_keys, *args, **kwargs):
        from distributed.protocol.serialize import ToPickle

        # Save "pre-stringified" key dependencies
        # TODO: Is there a better way to do this?
        state = self.layer_state.copy()
        state["layer_dependencies"] = self.layer_dependencies(all_hlg_keys)

        return ToPickle(state)

    @classmethod
    def __dask_distributed_unpack__(cls, state, dsk, dependencies):
        from distributed.protocol.serialize import ToPickle
        from distributed.utils_comm import unpack_remotedata
        from distributed.worker import dumps_task

        # Check if state is still a ToPickle object.
        # This can happen when a LocalCluster is
        # used with `processes=False`
        if isinstance(state, ToPickle):
            state = state.data

        # Pull out the pre-stringified layer deps
        # We need to know these depenendencies to
        # ensure we don't stringify tuples that
        # Dask may mistake for collection keys
        external_deps = state.pop("layer_dependencies", {})

        # Materialize the raw graph
        raw = cls.reconstructor()(**state).construct_graph()

        # Remove Future objects from the raw graph
        # and note any future dependencies
        raw2 = {}
        fut_deps = {}
        for k, v in raw.items():
            raw2[k], futs = unpack_remotedata(v, byte_keys=True)
            if futs:
                fut_deps[k] = futs
        raw = raw2

        # Build Layer dependencies (deps).
        # Start with set of known external dependencies
        deps = external_deps.copy()

        # Construct a set of all key used in this layer
        all_keys = set(raw.keys())
        for k, v in external_deps.items():
            all_keys.update(v)

        # Update deps with missing keys from raw
        missing_keys = raw.keys() - deps.keys()
        deps.update(
            (k, keys_in_tasks(all_keys, [raw[k]], as_list=False)) for k in missing_keys
        )

        # Update deps and all_keys with futures
        for k, futures in fut_deps.items():
            all_keys.update(f.key for f in futures)
            deps[k].update(f.key for f in futures)

        # Add local dependencies to deps
        deps.update({k: keys_in_tasks(all_keys, [v]) for k, v in raw.items()})

        # Stringify all keys in raw and deps
        deps = {stringify(k): {stringify(d) for d in v} for k, v in deps.items()}
        raw = {stringify(k): stringify(v, exclusive=all_keys) for k, v in raw.items()}

        # Must use `dumps_task` on the every task
        return {"dsk": toolz.valmap(dumps_task, raw), "deps": deps}


class MaterializedLayer(Layer):
    """Fully materialized layer of `Layer`

    Parameters
    ----------
    mapping: Mapping
        The mapping between keys and tasks, typically a dask graph.
    """

    def __init__(self, mapping: Mapping, annotations=None):
        super().__init__(annotations=annotations)
        self.mapping = mapping

    def __contains__(self, k):
        return k in self.mapping

    def __getitem__(self, k):
        return self.mapping[k]

    def __iter__(self):
        return iter(self.mapping)

    def __len__(self):
        return len(self.mapping)

    def is_materialized(self):
        return True

    def get_output_keys(self):
        return self.keys()


class HighLevelGraph(Mapping):
    """Task graph composed of layers of dependent subgraphs

    This object encodes a Dask task graph that is composed of layers of
    dependent subgraphs, such as commonly occurs when building task graphs
    using high level collections like Dask array, bag, or dataframe.

    Typically each high level array, bag, or dataframe operation takes the task
    graphs of the input collections, merges them, and then adds one or more new
    layers of tasks for the new operation.  These layers typically have at
    least as many tasks as there are partitions or chunks in the collection.
    The HighLevelGraph object stores the subgraphs for each operation
    separately in sub-graphs, and also stores the dependency structure between
    them.

    Parameters
    ----------
    layers : Mapping[str, Mapping]
        The subgraph layers, keyed by a unique name
    dependencies : Mapping[str, set[str]]
        The set of layers on which each layer depends
    key_dependencies : Mapping[Hashable, set], optional
        Mapping (some) keys in the high level graph to their dependencies. If
        a key is missing, its dependencies will be calculated on-the-fly.

    Examples
    --------
    Here is an idealized example that shows the internal state of a
    HighLevelGraph

    >>> import dask.dataframe as dd

    >>> df = dd.read_csv('myfile.*.csv')  # doctest: +SKIP
    >>> df = df + 100  # doctest: +SKIP
    >>> df = df[df.name == 'Alice']  # doctest: +SKIP

    >>> graph = df.__dask_graph__()  # doctest: +SKIP
    >>> graph.layers  # doctest: +SKIP
    {
     'read-csv': {('read-csv', 0): (pandas.read_csv, 'myfile.0.csv'),
                  ('read-csv', 1): (pandas.read_csv, 'myfile.1.csv'),
                  ('read-csv', 2): (pandas.read_csv, 'myfile.2.csv'),
                  ('read-csv', 3): (pandas.read_csv, 'myfile.3.csv')},
     'add': {('add', 0): (operator.add, ('read-csv', 0), 100),
             ('add', 1): (operator.add, ('read-csv', 1), 100),
             ('add', 2): (operator.add, ('read-csv', 2), 100),
             ('add', 3): (operator.add, ('read-csv', 3), 100)}
     'filter': {('filter', 0): (lambda part: part[part.name == 'Alice'], ('add', 0)),
                ('filter', 1): (lambda part: part[part.name == 'Alice'], ('add', 1)),
                ('filter', 2): (lambda part: part[part.name == 'Alice'], ('add', 2)),
                ('filter', 3): (lambda part: part[part.name == 'Alice'], ('add', 3))}
    }

    >>> graph.dependencies  # doctest: +SKIP
    {
     'read-csv': set(),
     'add': {'read-csv'},
     'filter': {'add'}
    }

    See Also
    --------
    HighLevelGraph.from_collections :
        typically used by developers to make new HighLevelGraphs
    """

    layers: Mapping[str, Layer]
    dependencies: Mapping[str, Set]
    key_dependencies: dict[Hashable, Set]
    _to_dict: dict
    _all_external_keys: set

    def __init__(
        self,
        layers: Mapping[str, Mapping],
        dependencies: Mapping[str, Set],
        key_dependencies: dict[Hashable, Set] | None = None,
    ):
        self.dependencies = dependencies
        self.key_dependencies = key_dependencies or {}
        # Makes sure that all layers are `Layer`
        self.layers = {
            k: v if isinstance(v, Layer) else MaterializedLayer(v)
            for k, v in layers.items()
        }

    @classmethod
    def _from_collection(cls, name, layer, collection):
        """`from_collections` optimized for a single collection"""
        if not is_dask_collection(collection):
            raise TypeError(type(collection))

        graph = collection.__dask_graph__()
        if isinstance(graph, HighLevelGraph):
            layers = ensure_dict(graph.layers, copy=True)
            layers[name] = layer
            deps = ensure_dict(graph.dependencies, copy=True)
            deps[name] = set(collection.__dask_layers__())
        else:
            key = _get_some_layer_name(collection)
            layers = {name: layer, key: graph}
            deps = {name: {key}, key: set()}

        return cls(layers, deps)

    @classmethod
    def from_collections(cls, name, layer, dependencies=()):
        """Construct a HighLevelGraph from a new layer and a set of collections

        This constructs a HighLevelGraph in the common case where we have a single
        new layer and a set of old collections on which we want to depend.

        This pulls out the ``__dask_layers__()`` method of the collections if
        they exist, and adds them to the dependencies for this new layer.  It
        also merges all of the layers from all of the dependent collections
        together into the new layers for this graph.

        Parameters
        ----------
        name : str
            The name of the new layer
        layer : Mapping
            The graph layer itself
        dependencies : List of Dask collections
            A list of other dask collections (like arrays or dataframes) that
            have graphs themselves

        Examples
        --------

        In typical usage we make a new task layer, and then pass that layer
        along with all dependent collections to this method.

        >>> def add(self, other):
        ...     name = 'add-' + tokenize(self, other)
        ...     layer = {(name, i): (add, input_key, other)
        ...              for i, input_key in enumerate(self.__dask_keys__())}
        ...     graph = HighLevelGraph.from_collections(name, layer, dependencies=[self])
        ...     return new_collection(name, graph)
        """
        if len(dependencies) == 1:
            return cls._from_collection(name, layer, dependencies[0])
        layers = {name: layer}
        deps = {name: set()}
        for collection in toolz.unique(dependencies, key=id):
            if is_dask_collection(collection):
                graph = collection.__dask_graph__()
                if isinstance(graph, HighLevelGraph):
                    layers.update(graph.layers)
                    deps.update(graph.dependencies)
                    deps[name] |= set(collection.__dask_layers__())
                else:
                    key = _get_some_layer_name(collection)
                    layers[key] = graph
                    deps[name].add(key)
                    deps[key] = set()
            else:
                raise TypeError(type(collection))

        return cls(layers, deps)

    def __getitem__(self, key):
        # Attempt O(1) direct access first, under the assumption that layer names match
        # either the keys (Scalar, Item, Delayed) or the first element of the key tuples
        # (Array, Bag, DataFrame, Series). This assumption is not always true.
        try:
            return self.layers[key][key]
        except KeyError:
            pass
        try:
            return self.layers[key[0]][key]
        except (KeyError, IndexError, TypeError):
            pass

        # Fall back to O(n) access
        for d in self.layers.values():
            try:
                return d[key]
            except KeyError:
                pass

        raise KeyError(key)

    def __len__(self) -> int:
        # NOTE: this will double-count keys that are duplicated between layers, so it's
        # possible that `len(hlg) > len(hlg.to_dict())`. However, duplicate keys should
        # not occur through normal use, and their existence would usually be a bug.
        # So we ignore this case in favor of better performance.
        # https://github.com/dask/dask/issues/7271
        return sum(len(layer) for layer in self.layers.values())

    def __iter__(self):
        return iter(self.to_dict())

    def to_dict(self) -> dict:
        """Efficiently convert to plain dict. This method is faster than dict(self)."""
        try:
            return self._to_dict
        except AttributeError:
            out = self._to_dict = ensure_dict(self)
            return out

    def keys(self) -> Set:
        """Get all keys of all the layers.

        This will in many cases materialize layers, which makes it a relatively
        expensive operation. See :meth:`get_all_external_keys` for a faster alternative.
        """
        return self.to_dict().keys()

    def get_all_external_keys(self) -> set:
        """Get all output keys of all layers

        This will in most cases _not_ materialize any layers, which makes
        it a relative cheap operation.

        Returns
        -------
        keys: set
            A set of all external keys
        """
        try:
            return self._all_external_keys
        except AttributeError:
            keys: set = set()
            for layer in self.layers.values():
                # Note: don't use `keys |= ...`, because the RHS is a
                # collections.abc.Set rather than a real set, and this will
                # cause a whole new set to be constructed.
                keys.update(layer.get_output_keys())
            self._all_external_keys = keys
            return keys

    def items(self):
        return self.to_dict().items()

    def values(self):
        return self.to_dict().values()

    def get_all_dependencies(self) -> dict[Hashable, Set]:
        """Get dependencies of all keys

        This will in most cases materialize all layers, which makes
        it an expensive operation.

        Returns
        -------
        map: Mapping
            A map that maps each key to its dependencies
        """
        all_keys = self.keys()
        missing_keys = all_keys - self.key_dependencies.keys()
        if missing_keys:
            for layer in self.layers.values():
                for k in missing_keys & layer.keys():
                    self.key_dependencies[k] = layer.get_dependencies(k, all_keys)
        return self.key_dependencies

    @property
    def dependents(self):
        return reverse_dict(self.dependencies)

    def copy(self):
        return HighLevelGraph(
            ensure_dict(self.layers, copy=True),
            ensure_dict(self.dependencies, copy=True),
            self.key_dependencies.copy(),
        )

    @classmethod
    def merge(cls, *graphs):
        layers = {}
        dependencies = {}
        for g in graphs:
            if isinstance(g, HighLevelGraph):
                layers.update(g.layers)
                dependencies.update(g.dependencies)
            elif isinstance(g, Mapping):
                layers[id(g)] = g
                dependencies[id(g)] = set()
            else:
                raise TypeError(g)
        return cls(layers, dependencies)

    def visualize(self, filename="dask-hlg.svg", format=None, **kwargs):
        """
        Visualize this dask high level graph.

        Requires ``graphviz`` to be installed.

        Parameters
        ----------
        filename : str or None, optional
            The name of the file to write to disk. If the provided `filename`
            doesn't include an extension, '.png' will be used by default.
            If `filename` is None, no file will be written, and the graph is
            rendered in the Jupyter notebook only.
        format : {'png', 'pdf', 'dot', 'svg', 'jpeg', 'jpg'}, optional
            Format in which to write output file. Default is 'svg'.
        color : {None, 'layer_type'}, optional (default: None)
            Options to color nodes.
            - None, no colors.
            - layer_type, color nodes based on the layer type.
        **kwargs
           Additional keyword arguments to forward to ``to_graphviz``.

        Examples
        --------
        >>> x.dask.visualize(filename='dask.svg')  # doctest: +SKIP
        >>> x.dask.visualize(filename='dask.svg', color='layer_type')  # doctest: +SKIP

        Returns
        -------
        result : IPython.diplay.Image, IPython.display.SVG, or None
            See dask.dot.dot_graph for more information.

        See Also
        --------
        dask.dot.dot_graph
        dask.base.visualize # low level variant
        """

        from .dot import graphviz_to_file

        g = to_graphviz(self, **kwargs)
        graphviz_to_file(g, filename, format)
        return g

    def _toposort_layers(self):
        """Sort the layers in a high level graph topologically

        Parameters
        ----------
        hlg : HighLevelGraph
            The high level graph's layers to sort

        Returns
        -------
        sorted: list
            List of layer names sorted topologically
        """
        degree = {k: len(v) for k, v in self.dependencies.items()}
        reverse_deps = {k: [] for k in self.dependencies}
        ready = []
        for k, v in self.dependencies.items():
            for dep in v:
                reverse_deps[dep].append(k)
            if not v:
                ready.append(k)
        ret = []
        while len(ready) > 0:
            layer = ready.pop()
            ret.append(layer)
            for rdep in reverse_deps[layer]:
                degree[rdep] -= 1
                if degree[rdep] == 0:
                    ready.append(rdep)
        return ret

    def cull(self, keys: Iterable) -> HighLevelGraph:
        """Return new HighLevelGraph with only the tasks required to calculate keys.

        In other words, remove unnecessary tasks from dask.

        Parameters
        ----------
        keys
            iterable of keys or nested list of keys such as the output of
            ``__dask_keys__()``

        Returns
        -------
        hlg: HighLevelGraph
            Culled high level graph
        """
        keys_set = set(flatten(keys))

        all_ext_keys = self.get_all_external_keys()
        ret_layers = {}
        ret_key_deps = {}
        for layer_name in reversed(self._toposort_layers()):
            layer = self.layers[layer_name]
            # Let's cull the layer to produce its part of `keys`.
            # Note: use .intersection rather than & because the RHS is
            # a collections.abc.Set rather than a real set, and using &
            # would take time proportional to the size of the LHS, which
            # if there is no culling can be much bigger than the RHS.
            output_keys = keys_set.intersection(layer.get_output_keys())
            if output_keys:
                culled_layer, culled_deps = layer.cull(output_keys, all_ext_keys)
                # Update `keys` with all layer's external key dependencies, which
                # are all the layer's dependencies (`culled_deps`) excluding
                # the layer's output keys.
                external_deps = set()
                for d in culled_deps.values():
                    external_deps |= d
                external_deps -= culled_layer.get_output_keys()
                keys_set |= external_deps

                # Save the culled layer and its key dependencies
                ret_layers[layer_name] = culled_layer
                ret_key_deps.update(culled_deps)

        # Converting dict_keys to a real set lets Python optimise the set
        # intersection to iterate over the smaller of the two sets.
        ret_layers_keys = set(ret_layers.keys())
        ret_dependencies = {
            layer_name: self.dependencies[layer_name] & ret_layers_keys
            for layer_name in ret_layers
        }

        return HighLevelGraph(ret_layers, ret_dependencies, ret_key_deps)

    def cull_layers(self, layers: Iterable[str]) -> HighLevelGraph:
        """Return a new HighLevelGraph with only the given layers and their
        dependencies. Internally, layers are not modified.

        This is a variant of :meth:`HighLevelGraph.cull` which is much faster and does
        not risk creating a collision between two layers with the same name and
        different content when two culled graphs are merged later on.

        Returns
        -------
        hlg: HighLevelGraph
            Culled high level graph
        """
        to_visit = set(layers)
        ret_layers = {}
        ret_dependencies = {}
        while to_visit:
            k = to_visit.pop()
            ret_layers[k] = self.layers[k]
            ret_dependencies[k] = self.dependencies[k]
            to_visit |= ret_dependencies[k] - ret_dependencies.keys()

        return HighLevelGraph(ret_layers, ret_dependencies)

    def validate(self):
        # Check dependencies
        for layer_name, deps in self.dependencies.items():
            if layer_name not in self.layers:
                raise ValueError(
                    f"dependencies[{repr(layer_name)}] not found in layers"
                )
            for dep in deps:
                if dep not in self.dependencies:
                    raise ValueError(f"{repr(dep)} not found in dependencies")

        for layer in self.layers.values():
            assert hasattr(layer, "annotations")

        # Re-calculate all layer dependencies
        dependencies = compute_layer_dependencies(self.layers)

        # Check keys
        dep_key1 = self.dependencies.keys()
        dep_key2 = dependencies.keys()
        if dep_key1 != dep_key2:
            raise ValueError(
                f"incorrect dependencies keys {set(dep_key1)!r} "
                f"expected {set(dep_key2)!r}"
            )

        # Check values
        for k in dep_key1:
            if self.dependencies[k] != dependencies[k]:
                raise ValueError(
                    f"incorrect dependencies[{repr(k)}]: {repr(self.dependencies[k])} "
                    f"expected {repr(dependencies[k])}"
                )

    def __dask_distributed_pack__(
        self,
        client,
        client_keys: Iterable[Hashable],
        annotations: Mapping[str, Any] = None,
    ) -> dict:
        """Pack the high level graph for Scheduler -> Worker communication

        The approach is to delegate the packaging to each layer in the high level graph
        by calling .__dask_distributed_pack__() and .__dask_distributed_annotations_pack__()
        on each layer.

        Parameters
        ----------
        client : distributed.Client
            The client calling this function.
        client_keys : Iterable[Hashable]
            List of keys requested by the client.
        annotations : Mapping[str, Any], optional
            A top-level annotations.

        Returns
        -------
        data: dict
            Packed high level graph layers
        """

        # If pickle is disabled on the scheduler, all layers must
        # be converted to `MaterializedLayer` objects before packing
        materialize = not config.get("distributed.scheduler.pickle")
        if not materialize:
            import distributed.protocol.serialize as s

            if not hasattr(s, "ToPickle"):
                # ToPickle not available in this version of
                # distributed - Materialize layers even if pickle=True
                materialize = True

        # Dump each layer (in topological order)
        layers = []
        for layer in (self.layers[name] for name in self._toposort_layers()):
            _layer = (
                MaterializedLayer(dict(layer), annotations=layer.annotations)
                if materialize and not isinstance(layer, MaterializedLayer)
                else layer
            )
            layers.append(
                {
                    "__module__": _layer.__module__,
                    "__name__": type(_layer).__name__,
                    "state": _layer.__dask_distributed_pack__(
                        self.get_all_external_keys(),
                        self.key_dependencies,
                        client,
                        client_keys,
                    ),
                    "annotations": _layer.__dask_distributed_annotations_pack__(
                        annotations
                    ),
                }
            )
        return {"layers": layers}

    @staticmethod
    def __dask_distributed_unpack__(hlg: dict) -> dict:
        """Unpack the high level graph for Scheduler -> Worker communication

        The approach is to delegate the unpackaging to each layer in the high level graph
        by calling ..._unpack__() and ..._annotations_unpack__()
        on each layer.

        Parameters
        ----------
        hlg: dict
            Packed high level graph layers

        Returns
        -------
        unpacked-graph: dict
            dsk: dict[str, Any]
                Materialized (stringified) graph of all nodes in the high level graph
            deps: dict[str, set]
                Dependencies of each key in `dsk`
            annotations: dict[str, Any]
                Annotations for `dsk`
        """
        from distributed.protocol.serialize import import_allowed_module

        dsk = {}
        deps = {}
        anno = {}

        # Unpack each layer (in topological order)
        for layer in hlg["layers"]:
            # Find the unpack functions
            if layer["__module__"] is None:  # Default implementation
                unpack_state = Layer.__dask_distributed_unpack__
                unpack_anno = Layer.__dask_distributed_annotations_unpack__
            else:
                mod = import_allowed_module(layer["__module__"])
                cls = getattr(mod, layer["__name__"])
                unpack_state = cls.__dask_distributed_unpack__
                unpack_anno = cls.__dask_distributed_annotations_unpack__

            # Unpack state into a graph and key dependencies
            unpacked_layer = unpack_state(layer["state"], dsk, deps)
            dsk.update(unpacked_layer["dsk"])
            for k, v in unpacked_layer["deps"].items():
                deps[k] = deps.get(k, set()) | v

            # Unpack the annotations
            unpack_anno(anno, layer["annotations"], unpacked_layer["dsk"].keys())
        return {"dsk": dsk, "deps": deps, "annotations": anno}

    def __repr__(self) -> str:
        representation = f"{type(self).__name__} with {len(self.layers)} layers.\n"
        representation += f"<{self.__class__.__module__}.{self.__class__.__name__} object at {hex(id(self))}>\n"
        for i, layerkey in enumerate(self._toposort_layers()):
            representation += f" {i}. {layerkey}\n"
        return representation

    def _repr_html_(self):
        return get_template("highlevelgraph.html.j2").render(
            type=type(self).__name__,
            layers=self.layers,
            toposort=self._toposort_layers(),
        )


def to_graphviz(
    hg,
    data_attributes=None,
    function_attributes=None,
    rankdir="BT",
    graph_attr=None,
    node_attr=None,
    edge_attr=None,
    **kwargs,
):
    from .dot import graphviz, label, name

    data_attributes = data_attributes or {}
    function_attributes = function_attributes or {}
    graph_attr = graph_attr or {}
    node_attr = node_attr or {}
    edge_attr = edge_attr or {}

    graph_attr["rankdir"] = rankdir
    node_attr["shape"] = "box"
    node_attr["fontname"] = "helvetica"

    graph_attr.update(kwargs)
    g = graphviz.Digraph(
        graph_attr=graph_attr, node_attr=node_attr, edge_attr=edge_attr
    )

    n_tasks = {}
    for layer in hg.dependencies:
        n_tasks[layer] = len(hg.layers[layer])

    min_tasks = min(n_tasks.values())
    max_tasks = max(n_tasks.values())

    cache = {}

    color = kwargs.get("color")
    if color == "layer_type":
        layer_colors = {
            "DataFrameIOLayer": ["#CCC7F9", False],  # purple
            "ShuffleLayer": ["#F9CCC7", False],  # rose
            "SimpleShuffleLayer": ["#F9CCC7", False],  # rose
            "ArrayOverlayLayer": ["#FFD9F2", False],  # pink
            "BroadcastJoinLayer": ["#D9F2FF", False],  # blue
            "Blockwise": ["#D9FFE6", False],  # green
            "BlockwiseLayer": ["#D9FFE6", False],  # green
            "MaterializedLayer": ["#DBDEE5", False],  # gray
        }

    for layer in hg.dependencies:
        layer_name = name(layer)
        attrs = data_attributes.get(layer, {})

        node_label = label(layer, cache=cache)
        node_size = (
            20
            if max_tasks == min_tasks
            else int(20 + ((n_tasks[layer] - min_tasks) / (max_tasks - min_tasks)) * 20)
        )

        layer_type = str(type(hg.layers[layer]).__name__)
        node_tooltips = (
            f"A {layer_type.replace('Layer', '')} Layer with {n_tasks[layer]} Tasks.\n"
        )

        layer_ca = hg.layers[layer].collection_annotations
        if layer_ca:
            if layer_ca.get("type") == "dask.array.core.Array":
                node_tooltips += (
                    f"Array Shape: {layer_ca.get('shape')}\n"
                    f"Data Type: {layer_ca.get('dtype')}\n"
                    f"Chunk Size: {layer_ca.get('chunksize')}\n"
                    f"Chunk Type: {layer_ca.get('chunk_type')}\n"
                )

            if layer_ca.get("type") == "dask.dataframe.core.DataFrame":
                dftype = {"pandas.core.frame.DataFrame": "pandas"}
                cols = layer_ca.get("columns")

                node_tooltips += (
                    f"Number of Partitions: {layer_ca.get('npartitions')}\n"
                    f"DataFrame Type: {dftype.get(layer_ca.get('dataframe_type'))}\n"
                    f"{len(cols)} DataFrame Columns: {str(cols) if len(str(cols)) <= 40 else '[...]'}\n"
                )

        attrs.setdefault("label", str(node_label))
        attrs.setdefault("fontsize", str(node_size))
        attrs.setdefault("tooltip", str(node_tooltips))

        if color == "layer_type":
            node_color = layer_colors.get(layer_type)[0]
            layer_colors.get(layer_type)[1] = True

            attrs.setdefault("fillcolor", str(node_color))
            attrs.setdefault("style", "filled")

        g.node(layer_name, **attrs)

    for layer, deps in hg.dependencies.items():
        layer_name = name(layer)
        for dep in deps:
            dep_name = name(dep)
            g.edge(dep_name, layer_name)

    if color == "layer_type":
        legend_title = "Key"

        legend_label = (
            '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="5">'
            "<TR><TD><B>Legend: Layer types</B></TD></TR>"
        )

        for layer_type, color in layer_colors.items():
            if color[1]:
                legend_label += f'<TR><TD BGCOLOR="{color[0]}">{layer_type}</TD></TR>'

        legend_label += "</TABLE>>"

        attrs = data_attributes.get(legend_title, {})
        attrs.setdefault("label", str(legend_label))
        attrs.setdefault("fontsize", "20")
        attrs.setdefault("margin", "0")

        g.node(legend_title, **attrs)

    return g


def _get_some_layer_name(collection) -> str:
    """Somehow get a unique name for a Layer from a non-HighLevelGraph dask mapping"""
    try:
        (name,) = collection.__dask_layers__()
        return name
    except (AttributeError, ValueError):
        # collection does not define the optional __dask_layers__ method
        # or it spuriously returns more than one layer
        return str(id(collection))
