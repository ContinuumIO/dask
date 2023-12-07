from __future__ import annotations

r""" Static order of nodes in dask graph

Dask makes decisions on what tasks to prioritize both

*  Dynamically at runtime
*  Statically before runtime

Dynamically we prefer to run tasks that were just made available.  However when
several tasks become available at the same time we have an opportunity to break
ties in an intelligent way

        d
        |
    b   c
     \ /
      a

For example after we finish ``a`` we can choose to run either ``b`` or ``c``
next.  Making small decisions like this can greatly affect our performance,
especially because the order in which we run tasks affects the order in which
we can release memory, which operationally we find to have a large affect on
many computation.  We want to run tasks in such a way that we keep only a small
amount of data in memory at any given time.


Static Ordering
---------------

And so we create a total ordering over all nodes to serve as a tie breaker.  We
represent this ordering with a dictionary mapping keys to integer values.
Lower scores have higher priority.  These scores correspond to the order in
which a sequential scheduler would visit each node.

    {'a': 0,
     'c': 1,
     'd': 2,
     'b': 3}

There are several ways in which we might order our keys.  This is a nuanced
process that has to take into account many different kinds of workflows, and
operate efficiently in linear time.  We strongly recommend that readers look at
the docstrings of tests in dask/tests/test_order.py.  These tests usually have
graph types laid out very carefully to show the kinds of situations that often
arise, and the order we would like to be determined.

"""
from collections import defaultdict, deque, namedtuple
from collections.abc import Mapping, MutableMapping
from typing import Any, Callable, Literal, NamedTuple, overload

from dask.core import get_dependencies, get_deps, getcycle, istask, reverse_dict
from dask.typing import Key


class Order(NamedTuple):
    priority: int
    critical_path: float | int


@overload
def order(
    dsk: Mapping[Key, Any],
    dependencies: Mapping[Key, set[Key]] | None = None,
    *,
    validate: bool = False,
    return_stats: Literal[True],
) -> dict[Key, Order]:
    ...


@overload
def order(
    dsk: Mapping[Key, Any],
    dependencies: Mapping[Key, set[Key]] | None = None,
    *,
    validate: bool = False,
    return_stats: Literal[False],
) -> dict[Key, int]:
    ...


def order(
    dsk: Mapping[Key, Any],
    dependencies: Mapping[Key, set[Key]] | None = None,
    *,
    validate: bool = False,
    return_stats: bool = False,
) -> dict[Key, Order] | dict[Key, int]:
    """Order nodes in dask graph

    This produces an ordering over our tasks that we use to break ties when
    executing.  We do this ahead of time to reduce a bit of stress on the
    scheduler and also to assist in static analysis.

    This currently traverses the graph as a single-threaded scheduler would
    traverse it.

    Examples
    --------
    >>> inc = lambda x: x + 1
    >>> add = lambda x, y: x + y
    >>> dsk = {'a': 1, 'b': 2, 'c': (inc, 'a'), 'd': (add, 'b', 'c')}
    >>> order(dsk)
    {'a': 0, 'c': 1, 'b': 2, 'd': 3}
    """
    if not dsk:
        return {}  # type: ignore

    dsk = dict(dsk)

    if dependencies is None:
        dependencies = {k: get_dependencies(dsk, k) for k in dsk}

    dependents = reverse_dict(dependencies)
    num_needed, total_dependencies = ndependencies(dependencies, dependents)
    if len(total_dependencies) != len(dsk):
        cycle = getcycle(dsk, None)
        raise RuntimeError(
            "Cycle detected between the following keys:\n  -> %s"
            % "\n  -> ".join(str(x) for x in cycle)
        )

    leaf_nodes = {k for k, v in dependents.items() if not v}
    root_nodes = {k for k, v in dependencies.items() if not v}
    assert dependencies is not None
    roots_connected = _connecting_to_roots(dependencies, dependents)
    leafs_connected = _connecting_to_roots(dependents, dependencies)
    result: dict[Key, Order | int] = {}
    i = 0
    linear_hull = set()
    runnable = list(k for k, v in dependencies.items() if not v)
    runnable = []
    known_runnable_paths: dict[Key, list[list[Key]]] = {}
    crit_path_counter = 0
    scrit_path: set[Key] = set()
    _crit_path_counter_offset = 0.0
    # We may want to process smaller groups first to get the chance to hit
    # connected subgraphs
    sort_keys = {
        # Constructing those tuples is relatively expensive if done during a
        # sorting operation. Therefore, compute it once and cache it
        x: (
            len(roots_connected[x]),
            total_dependencies[x],
            -max(len(dependents[k]) for k in roots_connected[x]),
            StrComparable(x),
        )
        for x in dsk
    }
    sort_key = sort_keys.__getitem__

    def add_to_result(item: Key) -> None:
        nonlocal crit_path_counter
        # Earlier versions recursed into this method but this could cause
        # recursion depth errors
        next_items = [item]
        nonlocal i
        while next_items:
            item = next_items.pop()
            assert not num_needed[item]
            linear_hull.discard(item)
            if item in result:
                continue
            if return_stats:
                result[item] = Order(i, crit_path_counter - _crit_path_counter_offset)
            else:
                result[item] = i
            i += 1
            # Note: This is a `set` and therefore this introduces a certain
            # randomness. However, this randomness should not have any impact on
            # the final result since the `process_runnable` should produce
            # equivalent results regardless of the order in which runnable is
            # populated (not identical but equivalent)
            for dep in dependents[item]:
                num_needed[dep] -= 1
                if not num_needed[dep]:
                    if len(dependents[item]) == 1:
                        next_items.append(dep)
                    else:
                        runnable.append(dep)

    def _with_offset(func: Callable[..., None]) -> Callable[..., None]:
        # This decorator is only used to reduce indentation levels. The offset
        # is purely cosmetical and used for some visualizations and I haven't
        # settled on how to implement this best so I didn't want to have large
        # indentations that make things harder to read
        nonlocal _crit_path_counter_offset

        def wrapper(*args: Any, **kwargs: Any) -> None:
            nonlocal _crit_path_counter_offset
            _crit_path_counter_offset = 0.5
            try:
                func(*args, **kwargs)
            finally:
                _crit_path_counter_offset = 0

        return wrapper

    @_with_offset
    def process_runnables() -> None:
        candidates = runnable.copy()
        runnable.clear()
        while candidates:
            key = candidates.pop()
            if key in linear_hull or key in result:
                continue
            if key in leaf_nodes:
                add_to_result(key)
                continue
            path = [key]
            branches = deque([path])
            while branches:
                path = branches.popleft()
                while True:
                    current = path[-1]
                    linear_hull.add(current)
                    deps_downstream = dependents[current]
                    deps_upstream = dependencies[current]  # type: ignore
                    if current in leaf_nodes:
                        # FIXME: The fact that it is possible for
                        # num_needed[current] == 0 means we're doing some work
                        # twice
                        if num_needed[current] <= 1 or (
                            not branches
                            # FIXME: This is a very magical number
                            and len(path) > 2
                        ):
                            for k in path[:-1]:
                                add_to_result(k)
                            if not num_needed[current]:
                                add_to_result(current)
                    elif len(path) == 1 or len(deps_upstream) == 1:
                        if len(deps_downstream) > 1:
                            for d in sorted(deps_downstream, key=sort_key):
                                # This ensures we're only considering splitters
                                # that are genuinely splitting and not
                                # interleaving
                                if len(dependencies[d]) == 1:  # type: ignore
                                    branch = path.copy()
                                    branch.append(d)
                                    branches.append(branch)
                            break
                        linear_hull.update(deps_downstream)
                        path.extend(sorted(deps_downstream, key=sort_key))
                        continue
                    elif current in known_runnable_paths:
                        known_runnable_paths[current].append(path)
                        if len(known_runnable_paths[current]) >= num_needed[current]:
                            pruned_branches: deque[list[Key]] = deque()
                            for path in known_runnable_paths.pop(current):
                                if path[-2] not in result:
                                    pruned_branches.append(path)
                            if len(pruned_branches) < num_needed[current]:
                                known_runnable_paths[current] = list(pruned_branches)
                            else:
                                if validate:
                                    nodes_in_branches = set()
                                    for b in pruned_branches:
                                        nodes_in_branches.update(b)
                                    cond = not (
                                        dependencies[current]  # type: ignore
                                        - set(result)
                                        - nodes_in_branches
                                    )
                                    assert cond
                                while pruned_branches:
                                    path = pruned_branches.popleft()
                                    for k in path:
                                        if num_needed[k]:
                                            pruned_branches.append(path)
                                            break
                                        add_to_result(k)
                    else:
                        if (
                            len(dependencies[current]) > 1  # type: ignore
                            and num_needed[current] <= 1
                        ):
                            for k in path:
                                add_to_result(k)
                        else:
                            known_runnable_paths[current] = [path]
                    break

    def pick_strategy() -> bool:
        # Note: We're trying to be smart here by picking a strategy on how to
        # determine the critical path. This is not always clear and we may want
        # to consider just calculating both orderings and picking the one with
        # less pressure. The only concern to this would be performance but at
        # time of writing, the most expensive part of ordering is the prep work
        # (various metrics, connected roots, etc.) which can be reused for
        # multiple orderings.
        size = 0
        if not abs(len(root_nodes) - len(leaf_nodes)) / len(root_nodes) > 0.8:
            # Heavy reducer / splitter topologies often benefit from a very
            # traditional critical path that expresses the longest chain of
            # tasks If there are disconnected subgraphs we will only pick a
            # longest path if the graph appears to be symmetric
            for r in root_nodes:
                if not size:
                    size = len(leafs_connected[r])
                elif size != len(leafs_connected[r]):
                    return False

        return True

    longest_path = pick_strategy()

    def get_target() -> Key:
        raise NotImplementedError()

    if not longest_path:

        def _build_get_target() -> Callable[[], Key]:
            # This is mutating `leafs_connected` !!
            occurences: defaultdict[Key, int] = defaultdict(int)
            for t in leaf_nodes:
                for r in roots_connected[t]:
                    occurences[r] += 1
            occurences_grouped = defaultdict(set)
            for root, occ in occurences.items():
                occurences_grouped[occ].add(root)
            del occurences
            most_valuable_leaf_sort_key = {}
            for root, leafs in leafs_connected.items():
                most_valuable_leaf_sort_key[root] = sort_keys[min(leafs, key=sort_key)]

            def seed_key(k: Key) -> tuple[tuple, tuple]:
                return (most_valuable_leaf_sort_key[k], sort_keys[k])

            def pick_seed() -> Key | None:
                while occurences_grouped:
                    key = max(occurences_grouped)
                    picked_root = min(occurences_grouped[key], key=seed_key)
                    if picked_root in result:
                        occurences_grouped[key].remove(picked_root)
                        if not occurences_grouped[key]:
                            del occurences_grouped[key]
                        continue
                    return picked_root
                return None

            def get_target() -> Key:
                # For asymmetric, weakly connected graphs we want to start
                # working on a branch that connects to the deepes / most
                # frequently used root. However, we also want to finish leafs as
                # fast as possible. Therefore, we want to pick the leaf with the
                # fewest root nodes required that connects to the most used
                # root.
                target = None
                candidates = leaf_nodes
                if linear_hull:
                    candidates = linear_hull & candidates
                if not candidates:
                    if seed := pick_seed():
                        candidates = leafs_connected[seed]
                    else:
                        candidates = linear_hull
                while not target and candidates:
                    target = min(
                        candidates, key=lambda k: (num_needed[k], sort_keys[k])
                    )
                    if target in result:
                        candidates.remove(target)
                        target = None
                assert target is not None
                return target

            return get_target

        get_target = _build_get_target()
    else:
        leaf_nodes_sorted = sorted(leaf_nodes, key=sort_key, reverse=False)
        get_target = leaf_nodes_sorted.pop

    # *************************************************************************
    # CORE ALGORITHM STARTS HERE
    #
    # A. Build the critical path
    #
    #   To build the critical path we will use a provided `get_target` function
    #   that returns a node that is anywhere in the graph, typically a leaf
    #   node. This node is not required to be runnable. We will walk the graph
    #   backwards and append nodes to the graph as we go. The critical path is a
    #   linear path in the graph. While this is a viable strategy, it is not
    #   required for the critical path to be a classical "longest path" but it
    #   can define any route through the graph that should be considered as top
    #   priority.
    #
    #   1. Determine the target node by calling `get_target`` and append the
    #      target to the critical path stack
    #   2. Take the _most valuable_ (max given a `sort_key`) of it's dependends
    #      and append it to the critical path stack. This key is the new target.
    #   3. Repeat step 2 until we reach a node that has no dependencies and is
    #      therefore runnable
    #
    # B. Walk the critical path
    #
    #   Only the first element of the critical path is an actually runnable node
    #   and this is where we're starting the sort. Strategically, this is the
    #   most important goal to achieve but since not all of the nodes are
    #   immediately runnable we have to walk back and compute other nodes first
    #   before we can unlock the critical path. This typically requires us also
    #   to load more data / run more root tasks.
    #   While walking the critical path we will also unlock non-critical tasks
    #   that could be run but are not contributing to our strategic goal. Under
    #   certain circumstances, those runnable tasks are allowed to be run right
    #   away to reduce memory pressure. This is described in more detail in
    #   `process_runnable`.
    #   Given this, the algorithm is as follows:
    #
    #   1. Pop the first element of the critical path
    #   2a. If the node is already in the result, continue
    #   2b. If the node is not runnable, we will put it back on the stack and
    #       put all its dependencies on the stack and continue with step 1. This
    #       is what we refer to as "walking back"
    #   2c. Else, we add the node to the result
    #   3.  If we previously had to walk back we will consider running
    #       non-critical tasks (by calling process_runnables)
    #   4a. If critical path is not empty, repeat step 1
    #   4b. Go back to A.) and build a new critical path given a new target that
    #       accounts for the already computed nodes.
    #
    # *************************************************************************

    while len(result) < len(dsk):
        crit_path_counter += 1

        # A. Build the critical path
        target = get_target()
        next_deps = dependencies[target]
        critical_path = [target]
        scrit_path.clear()
        scrit_path.add(target)
        while next_deps:
            item = max(next_deps, key=sort_key)
            critical_path.append(item)
            next_deps = dependencies[item]
            scrit_path.update(next_deps)

        # B. Walk the critical path

        walked_back = False
        # If there is no linear hull, we'll ignore this
        while critical_path:
            item = critical_path.pop()
            scrit_path.discard(item)
            if item in result:
                continue
            if num_needed[item]:
                if item in known_runnable_paths:
                    for path in known_runnable_paths.pop(item):
                        critical_path.extend(path[::-1])
                        scrit_path.update(path[::-1])
                    continue
                critical_path.append(item)
                scrit_path.add(item)
                deps = dependencies[item].difference(result)
                unknown = []
                known = []
                for d in sorted(deps, key=sort_key):
                    if d in known_runnable_paths:
                        known.append(d)
                    else:
                        unknown.append(d)
                if len(unknown) > 1:
                    walked_back = True

                for d in unknown:
                    critical_path.append(d)
                    scrit_path.add(d)
                for d in known:
                    for path in known_runnable_paths.pop(d):
                        critical_path.extend(path[::-1])
                        scrit_path.update(path[::-1])

                del deps
                continue
            else:
                if walked_back and len(runnable) < len(critical_path):
                    process_runnables()
                add_to_result(item)
        process_runnables()

    return result  # type: ignore


def _connecting_to_roots(
    dependencies: Mapping[Key, set[Key]], dependents: Mapping[Key, set[Key]]
) -> dict[Key, set[Key]]:
    """Determine for every node which root nodes are connected to it (i.e.
    ancestors). If arguments of dependencies and depentends are switched, this
    can also be used to determine which leaf nodes are connected to which node
    (i.e. descendants)."""
    num_needed = {}
    result = {}
    current = []
    num_needed = {k: len(v) for k, v in dependencies.items() if v}
    for k, v in dependencies.items():
        if not v:
            result[k] = {k}
            for child in dependents[k]:
                num_needed[child] -= 1
                if not num_needed[child]:
                    current.append(child)
    while current:
        key = current.pop()
        for child in dependents[key]:
            num_needed[child] -= 1
            if not num_needed[child]:
                current.append(child)
        # At some point, all the roots are the same, particualarly for dense
        # graphs. We don't want to create new sets over and over again
        new_set = set()
        previous: set[Key] = set()
        identical_sets = True
        for parent in dependencies[key]:
            if not previous:
                previous = result[parent]
            elif identical_sets and previous is result[parent]:
                identical_sets = True
            else:
                identical_sets = False
                new_set.update(result[parent])
        if identical_sets:
            result[key] = previous
        else:
            new_set.update(previous)
            result[key] = new_set
    return result


def ndependencies(
    dependencies: Mapping[Key, set[Key]], dependents: Mapping[Key, set[Key]]
) -> tuple[dict[Key, int], dict[Key, int]]:
    """Number of total data elements on which this key depends

    For each key we return the number of tasks that must be run for us to run
    this task.

    Examples
    --------
    >>> inc = lambda x: x + 1
    >>> dsk = {'a': 1, 'b': (inc, 'a'), 'c': (inc, 'b')}
    >>> dependencies, dependents = get_deps(dsk)
    >>> num_dependencies, total_dependencies = ndependencies(dependencies, dependents)
    >>> sorted(total_dependencies.items())
    [('a', 1), ('b', 2), ('c', 3)]

    Returns
    -------
    num_dependencies: Dict[key, int]
    total_dependencies: Dict[key, int]
    """
    num_needed = {}
    result = {}
    for k, v in dependencies.items():
        num_needed[k] = len(v)
        if not v:
            result[k] = 1

    num_dependencies = num_needed.copy()
    current: list[Key] = []
    current_pop = current.pop
    current_append = current.append

    for key in result:
        for parent in dependents[key]:
            num_needed[parent] -= 1
            if not num_needed[parent]:
                current_append(parent)
    while current:
        key = current_pop()
        result[key] = 1 + sum(result[child] for child in dependencies[key])
        for parent in dependents[key]:
            num_needed[parent] -= 1
            if not num_needed[parent]:
                current_append(parent)
    return num_dependencies, result


class StrComparable:
    """Wrap object so that it defaults to string comparison

    When comparing two objects of different types Python fails

    >>> 'a' < 1
    Traceback (most recent call last):
        ...
    TypeError: '<' not supported between instances of 'str' and 'int'

    This class wraps the object so that, when this would occur it instead
    compares the string representation

    >>> StrComparable('a') < StrComparable(1)
    False
    """

    __slots__ = ("obj",)

    obj: Any

    def __init__(self, obj: Any):
        self.obj = obj

    def __lt__(self, other: Any) -> bool:
        try:
            return self.obj < other.obj
        except Exception:
            return str(self.obj) < str(other.obj)


OrderInfo = namedtuple(
    "OrderInfo",
    (
        "order",
        "age",
        "num_data_when_run",
        "num_data_when_released",
        "num_dependencies_freed",
    ),
)


def diagnostics(
    dsk: MutableMapping[Key, Any],
    o: Mapping[Key, int] | None = None,
    dependencies: MutableMapping[Key, set[Key]] | None = None,
) -> tuple[dict[Key, OrderInfo], list[int]]:
    """Simulate runtime metrics as though running tasks one at a time in order.

    These diagnostics can help reveal behaviors of and issues with ``order``.

    Returns a dict of `namedtuple("OrderInfo")` and a list of the number of outputs held over time.

    OrderInfo fields:
    - order : the order in which the node is run.
    - age : how long the output of a node is held.
    - num_data_when_run : the number of outputs held in memory when a node is run.
    - num_data_when_released : the number of outputs held in memory when the output is released.
    - num_dependencies_freed : the number of dependencies freed by running the node.
    """
    if dependencies is None:
        dependencies, dependents = get_deps(dsk)
    else:
        dependents = reverse_dict(dependencies)
    assert dependencies is not None
    if o is None:
        o = order(dsk, dependencies=dependencies, return_stats=False)

    pressure = []
    num_in_memory = 0
    age = {}
    runpressure = {}
    releasepressure = {}
    freed = {}
    num_needed = {key: len(val) for key, val in dependents.items()}
    for i, key in enumerate(sorted(dsk, key=o.__getitem__)):
        pressure.append(num_in_memory)
        runpressure[key] = num_in_memory
        released = 0
        for dep in dependencies[key]:
            num_needed[dep] -= 1
            if num_needed[dep] == 0:
                age[dep] = i - o[dep]
                releasepressure[dep] = num_in_memory
                released += 1
        freed[key] = released
        if dependents[key]:
            num_in_memory -= released - 1
        else:
            age[key] = 0
            releasepressure[key] = num_in_memory
            num_in_memory -= released

    rv = {
        key: OrderInfo(
            val, age[key], runpressure[key], releasepressure[key], freed[key]
        )
        for key, val in o.items()
    }
    return rv, pressure


def _f() -> None:
    ...


def _convert_task(task: Any) -> Any:
    if istask(task):
        assert callable(task[0])
        new_spec: list[Any] = []
        for el in task[1:]:
            if isinstance(el, (str, int)):
                new_spec.append(el)
            elif isinstance(el, tuple):
                if istask(el):
                    new_spec.append(_convert_task(el))
                else:
                    new_spec.append(el)
            elif isinstance(el, list):
                new_spec.append([_convert_task(e) for e in el])
        return (_f, *new_spec)
    elif isinstance(task, tuple):
        return (_f, task)
    else:
        return (_f, *task)


def sanitize_dsk(dsk: MutableMapping[Key, Any]) -> dict:
    """Take a dask graph and replace callables with a dummy function and remove
    payload data like numpy arrays, dataframes, etc.
    """
    new = {}
    for key, values in dsk.items():
        new_key = key
        new[new_key] = _convert_task(values)
    if get_deps(new) != get_deps(dsk):
        # The switch statement in _convert likely dropped some keys
        raise RuntimeError("Sanitization failed to preserve topology.")
    return new
