import functools
import math
import operator

from dask.core import flatten
from dask.dataframe.dispatch import make_meta, meta_nonempty
from dask.dataframe.multi import _concat_wrapper, _merge_chunk_wrapper, _split_partition
from dask.dataframe.shuffle import partitioning_index
from dask.utils import M, apply, get_default_shuffle_method
from toolz import merge_sorted, unique

from dask_expr._expr import (
    Blockwise,
    Expr,
    Filter,
    Index,
    PartitionsFiltered,
    Projection,
)
from dask_expr._repartition import Repartition
from dask_expr._shuffle import (
    AssignPartitioningIndex,
    Shuffle,
    _contains_index_name,
    _select_columns_or_index,
)
from dask_expr._util import _convert_to_list, _tokenize_deterministic

_HASH_COLUMN_NAME = "__hash_partition"
_PARTITION_COLUMN = "_partitions"


class Merge(Expr):
    """Merge / join two dataframes

    This is an abstract class.  It will be transformed into a concrete
    implementation before graph construction.

    See Also
    --------
    BlockwiseMerge
    Repartition
    Shuffle
    """

    _parameters = [
        "left",
        "right",
        "how",
        "left_on",
        "right_on",
        "left_index",
        "right_index",
        "suffixes",
        "indicator",
        "shuffle_backend",
        "_npartitions",
        "broadcast",
    ]
    _defaults = {
        "how": "inner",
        "left_on": None,
        "right_on": None,
        "left_index": False,
        "right_index": False,
        "suffixes": ("_x", "_y"),
        "indicator": False,
        "shuffle_backend": None,
        "_npartitions": None,
        "broadcast": None,
    }

    # combine similar variables
    _skip_ops = (Filter, AssignPartitioningIndex, Shuffle)
    _remove_ops = (Projection,)

    def __str__(self):
        return f"Merge({self._name[-7:]})"

    @property
    def kwargs(self):
        return {
            k: self.operand(k)
            for k in [
                "how",
                "left_on",
                "right_on",
                "left_index",
                "right_index",
                "suffixes",
                "indicator",
            ]
        }

    @functools.cached_property
    def _meta(self):
        left = meta_nonempty(self.left._meta)
        right = meta_nonempty(self.right._meta)
        return make_meta(left.merge(right, **self.kwargs))

    @functools.cached_property
    def _npartitions(self):
        if self.operand("_npartitions") is not None:
            return self.operand("_npartitions")
        return max(self.left.npartitions, self.right.npartitions)

    def _divisions(self):
        if self.merge_indexed_left and self.merge_indexed_right:
            return list(unique(merge_sorted(self.left.divisions, self.right.divisions)))

        if self.is_broadcast_join:
            if self.broadcast_side == "left":
                return self.right._divisions()
            return self.left._divisions()

        if self._is_single_partition_broadcast:
            _npartitions = max(self.left.npartitions, self.right.npartitions)
        else:
            _npartitions = self._npartitions

        return (None,) * (_npartitions + 1)

    @functools.cached_property
    def broadcast_side(self):
        return "left" if self.left.npartitions < self.right.npartitions else "right"

    @functools.cached_property
    def is_broadcast_join(self):
        broadcast_bias, broadcast = 0.5, None
        broadcast_side = self.broadcast_side
        if isinstance(self.broadcast, float):
            broadcast_bias = self.broadcast
        elif isinstance(self.broadcast, bool):
            broadcast = self.broadcast

        s_backend = self.shuffle_backend or get_default_shuffle_method()
        if (
            s_backend in ("tasks", "p2p")
            and self.how in ("inner", "left", "right")
            and self.how != broadcast_side
            and broadcast is not False
        ):
            n_low = min(self.left.npartitions, self.right.npartitions)
            n_high = max(self.left.npartitions, self.right.npartitions)
            if broadcast or (n_low < math.log2(n_high) * broadcast_bias):
                return True
        return False

    @functools.cached_property
    def _is_single_partition_broadcast(self):
        _npartitions = max(self.left.npartitions, self.right.npartitions)
        return (
            _npartitions == 1
            or self.left.npartitions == 1
            and self.how in ("right", "inner")
            or self.right.npartitions == 1
            and self.how in ("left", "inner")
        )

    @functools.cached_property
    def merge_indexed_left(self):
        return (
            self.left_index or _contains_index_name(self.left, self.left_on)
        ) and self.left.known_divisions

    @functools.cached_property
    def merge_indexed_right(self):
        return (
            self.right_index or _contains_index_name(self.right, self.right_on)
        ) and self.right.known_divisions

    @functools.cached_property
    def merge_indexed_left(self):
        return (
            self.left_index or _contains_index_name(self.left, self.left_on)
        ) and self.left.known_divisions

    @functools.cached_property
    def merge_indexed_right(self):
        return (
            self.right_index or _contains_index_name(self.right, self.right_on)
        ) and self.right.known_divisions

    def _lower(self):
        # Lower from an abstract expression
        left = self.left
        right = self.right
        left_on = self.left_on
        right_on = self.right_on
        left_index = self.left_index
        right_index = self.right_index
        shuffle_backend = self.shuffle_backend

        # TODO:
        #  1. Add/leverage partition statistics

        # Check for "trivial" broadcast (single partition)
        if self._is_single_partition_broadcast:
            return BlockwiseMerge(left, right, **self.kwargs)

        # NOTE: Merging on an index is fragile. Pandas behavior
        # depends on the actual data, and so we cannot use `meta`
        # to accurately predict the output columns. Once general
        # partition statistics are available, it may make sense
        # to drop support for left_index and right_index.

        shuffle_left_on = left_on
        shuffle_right_on = right_on
        if self.merge_indexed_left and self.merge_indexed_right:
            # fully-indexed merge
            divisions = list(unique(merge_sorted(left.divisions, right.divisions)))
            right = Repartition(right, new_divisions=divisions, force=True)
            left = Repartition(left, new_divisions=divisions, force=True)
            shuffle_left_on = shuffle_right_on = None

        # TODO:
        #   - Need 'rearrange_by_divisions' equivalent
        #     to avoid shuffle when we are merging on known
        #     divisions on one side only.
        else:
            if left_index:
                shuffle_left_on = left.index._meta.name
                if shuffle_left_on is None:
                    # placeholder for unnamed index merge
                    shuffle_left_on = "_index"
            if right_index:
                shuffle_right_on = right.index._meta.name
                if shuffle_right_on is None:
                    shuffle_right_on = "_index"

            if self.is_broadcast_join:
                if self.operand("_npartitions") is not None:
                    if self.broadcast_side == "right":
                        left = Repartition(left, new_partitions=self._npartitions)
                    else:
                        right = Repartition(right, new_partitions=self._npartitions)

                if self.how != "inner":
                    if self.broadcast_side == "left":
                        left = Shuffle(
                            left,
                            shuffle_left_on,
                            npartitions_out=right.npartitions,
                        )
                    else:
                        right = Shuffle(
                            right,
                            shuffle_right_on,
                            npartitions_out=right.npartitions,
                        )

                return BroadcastJoin(
                    left,
                    right,
                    self.how,
                    left_on,
                    right_on,
                    left_index,
                    right_index,
                    self.suffixes,
                    self.indicator,
                )

        if (shuffle_left_on or shuffle_right_on) and (
            shuffle_backend == "p2p"
            or shuffle_backend is None
            and get_default_shuffle_method() == "p2p"
        ):
            return HashJoinP2P(
                left,
                right,
                how=self.how,
                left_on=left_on,
                right_on=right_on,
                suffixes=self.suffixes,
                indicator=self.indicator,
                left_index=left_index,
                right_index=right_index,
                shuffle_left_on=shuffle_left_on,
                shuffle_right_on=shuffle_right_on,
                _npartitions=self.operand("_npartitions"),
            )

        if shuffle_left_on:
            # Shuffle left
            left = Shuffle(
                left,
                shuffle_left_on,
                npartitions_out=self._npartitions,
                backend=shuffle_backend,
                index_shuffle=left_index,
            )

        if shuffle_right_on:
            # Shuffle right
            right = Shuffle(
                right,
                shuffle_right_on,
                npartitions_out=self._npartitions,
                backend=shuffle_backend,
                index_shuffle=right_index,
            )

        # Blockwise merge
        return BlockwiseMerge(left, right, **self.kwargs)

    def _simplify_up(self, parent):
        if isinstance(parent, (Projection, Index)):
            # Reorder the column projection to
            # occur before the Merge
            if isinstance(parent, Index):
                # Index creates an empty column projection
                projection, parent_columns = [], None
            else:
                projection, parent_columns = parent.operand("columns"), parent.operand(
                    "columns"
                )
                if isinstance(projection, (str, int)):
                    projection = [projection]

            left, right = self.left, self.right
            left_on = _convert_to_list(self.left_on)
            if left_on is None:
                left_on = []

            right_on = _convert_to_list(self.right_on)
            if right_on is None:
                right_on = []

            left_suffix, right_suffix = self.suffixes[0], self.suffixes[1]
            project_left, project_right = [], []

            # Find columns to project on the left
            for col in left.columns:
                if col in left_on or col in projection:
                    project_left.append(col)
                elif f"{col}{left_suffix}" in projection:
                    project_left.append(col)
                    if col in right.columns:
                        # Right column must be present
                        # for the suffix to be applied
                        project_right.append(col)

            # Find columns to project on the right
            for col in right.columns:
                if col in right_on or col in projection:
                    project_right.append(col)
                elif f"{col}{right_suffix}" in projection:
                    project_right.append(col)
                    if col in left.columns and col not in project_left:
                        # Left column must be present
                        # for the suffix to be applied
                        project_left.append(col)

            if set(project_left) < set(left.columns) or set(project_right) < set(
                right.columns
            ):
                result = type(self)(
                    left[project_left], right[project_right], *self.operands[2:]
                )
                if parent_columns is None:
                    return type(parent)(result)
                return result[parent_columns]

    def _validate_same_operations(self, common, op, remove="both"):
        # Travers left and right to check if we can find the same operation
        # more than once. We have to account for potential projections on both sides
        name = common._name
        if name == op._name:
            return True, op.left.columns, op.right.columns

        columns_left, columns_right = None, None
        op_left, op_right = op.left, op.right
        if remove in ("both", "left"):
            op_left, columns_left = self._remove_operations(
                op.left, self._remove_ops, self._skip_ops
            )
        if remove in ("both", "right"):
            op_right, columns_right = self._remove_operations(
                op.right, self._remove_ops, self._skip_ops
            )

        return (
            type(op)(op_left, op_right, *op.operands[2:])._name == name,
            columns_left,
            columns_right,
        )

    @staticmethod
    def _flatten_columns(expr, columns, side):
        if len(columns) == 0:
            return getattr(expr, side).columns
        else:
            return list(set(flatten(columns)))

    def _combine_similar(self, root: Expr):
        # Push projections back up to avoid performing the same merge multiple times

        left, columns_left = self._remove_operations(
            self.left, self._remove_ops, self._skip_ops
        )
        columns_left = self._flatten_columns(self, columns_left, "left")
        right, columns_right = self._remove_operations(
            self.right, self._remove_ops, self._skip_ops
        )
        columns_right = self._flatten_columns(self, columns_right, "right")

        if left._name == self.left._name and right._name == self.right._name:
            # There aren't any ops we can remove, so bail
            return

        # We can not remove Projections on both sides at once, because only
        # one side might need the push back up step. So try if removing Projections
        # on either side works before removing them on both sides at once.

        common_left = type(self)(self.left, right, *self.operands[2:])
        common_right = type(self)(left, self.right, *self.operands[2:])
        common_both = type(self)(left, right, *self.operands[2:])

        columns, left_sub, right_sub = None, None, None

        for op in self._find_similar_operations(root, ignore=["left", "right"]):
            if op._name in (common_right._name, common_left._name, common_both._name):
                if sorted(self.columns) != sorted(op.columns):
                    return op[self.columns]
                return op

            validation = self._validate_same_operations(common_right, op, "left")
            if validation[0]:
                left_sub = self._flatten_columns(op, validation[1], side="left")
                columns = self.right.columns.copy()
                columns += [col for col in self.left.columns if col not in columns]
                break

            validation = self._validate_same_operations(common_left, op, "right")
            if validation[0]:
                right_sub = self._flatten_columns(op, validation[2], side="right")
                columns = self.left.columns.copy()
                columns += [col for col in self.right.columns if col not in columns]
                break

            validation = self._validate_same_operations(common_both, op)
            if validation[0]:
                left_sub = self._flatten_columns(op, validation[1], side="left")
                right_sub = self._flatten_columns(op, validation[2], side="right")
                columns = columns_left.copy()
                columns += [col for col in columns_right if col not in columns_left]
                break

        if columns is not None:
            expr = self
            if _PARTITION_COLUMN in columns:
                columns.remove(_PARTITION_COLUMN)

            if left_sub is not None:
                left_sub.extend([col for col in columns_left if col not in left_sub])
                left = self._replace_projections(self.left, sorted(left_sub))
                expr = expr.substitute(self.left, left)

            if right_sub is not None:
                right_sub.extend([col for col in columns_right if col not in right_sub])
                right = self._replace_projections(self.right, sorted(right_sub))
                expr = expr.substitute(self.right, right)

            if sorted(expr.columns) != sorted(columns):
                expr = expr[columns]
            if expr._name == self._name:
                return None
            return expr

    def _replace_projections(self, frame, new_columns):
        # This branch might have a number of Projections that differ from our
        # new columns. We replace those projections appropriately

        operations = []
        while isinstance(frame, self._remove_ops + self._skip_ops):
            if isinstance(frame, self._remove_ops):
                # TODO: Shuffle and AssignPartitioningIndex being 2 different ops
                #  causes all kinds of pain
                if isinstance(frame.frame, AssignPartitioningIndex):
                    new_cols = new_columns
                else:
                    new_cols = [col for col in new_columns if col != _PARTITION_COLUMN]

                # Ignore Projection if new_columns = frame.frame.columns
                if sorted(new_cols) != sorted(frame.frame.columns):
                    operations.append((type(frame), [new_cols]))
            else:
                operations.append((type(frame), frame.operands[1:]))
            frame = frame.frame

        for op_type, operands in reversed(operations):
            frame = op_type(frame, *operands)
        return frame


class HashJoinP2P(Merge, PartitionsFiltered):
    _parameters = [
        "left",
        "right",
        "how",
        "left_on",
        "right_on",
        "left_index",
        "right_index",
        "suffixes",
        "indicator",
        "_partitions",
        "shuffle_left_on",
        "shuffle_right_on",
        "_npartitions",
    ]
    _defaults = {
        "how": "inner",
        "left_on": None,
        "right_on": None,
        "left_index": None,
        "right_index": None,
        "suffixes": ("_x", "_y"),
        "indicator": False,
        "_partitions": None,
        "shuffle_left_on": None,
        "shuffle_right_on": None,
        "_npartitions": None,
    }
    is_broadcast_join = False

    def _lower(self):
        return None

    def _layer(self) -> dict:
        from distributed.shuffle._core import ShuffleId, barrier_key
        from distributed.shuffle._merge import merge_unpack
        from distributed.shuffle._shuffle import shuffle_barrier

        dsk = {}
        token_left = _tokenize_deterministic(
            "hash-join",
            self.left._name,
            self.shuffle_left_on,
            self.npartitions,
            self._partitions,
        )
        token_right = _tokenize_deterministic(
            "hash-join",
            self.right._name,
            self.shuffle_right_on,
            self.npartitions,
            self._partitions,
        )
        _barrier_key_left = barrier_key(ShuffleId(token_left))
        _barrier_key_right = barrier_key(ShuffleId(token_right))

        transfer_name_left = "hash-join-transfer-" + token_left
        transfer_name_right = "hash-join-transfer-" + token_right
        transfer_keys_left = list()
        transfer_keys_right = list()
        func = create_assign_index_merge_transfer()
        for i in range(self.left.npartitions):
            transfer_keys_left.append((transfer_name_left, i))
            dsk[(transfer_name_left, i)] = (
                func,
                (self.left._name, i),
                self.shuffle_left_on,
                _HASH_COLUMN_NAME,
                self.npartitions,
                token_left,
                i,
                self.left._meta,
                self._partitions,
                self.left_index,
            )
        for i in range(self.right.npartitions):
            transfer_keys_right.append((transfer_name_right, i))
            dsk[(transfer_name_right, i)] = (
                func,
                (self.right._name, i),
                self.shuffle_right_on,
                _HASH_COLUMN_NAME,
                self.npartitions,
                token_right,
                i,
                self.right._meta,
                self._partitions,
                self.right_index,
            )

        dsk[_barrier_key_left] = (shuffle_barrier, token_left, transfer_keys_left)
        dsk[_barrier_key_right] = (
            shuffle_barrier,
            token_right,
            transfer_keys_right,
        )

        for part_out in self._partitions:
            dsk[(self._name, part_out)] = (
                merge_unpack,
                token_left,
                token_right,
                part_out,
                _barrier_key_left,
                _barrier_key_right,
                self.how,
                self.left_on,
                self.right_on,
                self._meta,
                self.suffixes,
                self.left_index,
                self.right_index,
            )
        return dsk

    def _simplify_up(self, parent):
        return


class BroadcastJoin(Merge, PartitionsFiltered):
    _parameters = [
        "left",
        "right",
        "how",
        "left_on",
        "right_on",
        "left_index",
        "right_index",
        "suffixes",
        "indicator",
        "_partitions",
    ]
    _defaults = {
        "how": "inner",
        "left_on": None,
        "right_on": None,
        "left_index": None,
        "right_index": None,
        "suffixes": ("_x", "_y"),
        "indicator": False,
        "_partitions": None,
    }

    def _divisions(self):
        if self.broadcast_side == "left":
            return self.right._divisions()
        return self.left._divisions()

    def _simplify_up(self, parent):
        return

    def _lower(self):
        return None

    def _layer(self) -> dict:
        if self.broadcast_side == "left":
            bcast_name = self.left._name
            bcast_size = self.left.npartitions
            other = self.right._name
            other_on = self.right_on
        else:
            bcast_name = self.right._name
            bcast_size = self.right.npartitions
            other = self.left._name
            other_on = self.left_on

        split_name = "split-" + self._name
        inter_name = "inter-" + self._name
        kwargs = {
            "how": self.how,
            "indicator": self.indicator,
            "left_index": self.left_index,
            "right_index": self.right_index,
            "suffixes": self.suffixes,
            "result_meta": self._meta,
            "left_on": self.left_on,
            "right_on": self.right_on,
        }
        dsk = {}
        for part_out in self._partitions:
            if self.how != "inner":
                dsk[(split_name, part_out)] = (
                    _split_partition,
                    (other, part_out),
                    other_on,
                    bcast_size,
                )

            _concat_list = []
            for j in range(bcast_size):
                # Specify arg list for `merge_chunk`
                _merge_args = [
                    (
                        operator.getitem,
                        (split_name, part_out),
                        j,
                    )
                    if self.how != "inner"
                    else (other, part_out),
                    (bcast_name, j),
                ]
                if self.broadcast_side == "left":
                    _merge_args.reverse()

                inter_key = (inter_name, part_out, j)
                dsk[(inter_name, part_out, j)] = (
                    apply,
                    _merge_chunk_wrapper,
                    _merge_args,
                    kwargs,
                )
                _concat_list.append(inter_key)
            dsk[(self._name, part_out)] = (_concat_wrapper, _concat_list)
        return dsk


def create_assign_index_merge_transfer():
    import pandas as pd
    from distributed.shuffle._core import ShuffleId
    from distributed.shuffle._merge import merge_transfer

    def assign_index_merge_transfer(
        df,
        index,
        name,
        npartitions,
        id: ShuffleId,
        input_partition: int,
        meta: pd.DataFrame,
        parts_out: set[int],
        index_merge,
    ):
        if index_merge:
            index = df[[]]
            index["_index"] = df.index
        else:
            index = _select_columns_or_index(df, index)
        if isinstance(index, (str, list, tuple)):
            # Assume column selection from df
            index = [index] if isinstance(index, str) else list(index)
            index = partitioning_index(df[index], npartitions)
        else:
            index = partitioning_index(index, npartitions)
        df = df.assign(**{name: index})
        meta = meta.assign(**{name: 0})
        return merge_transfer(
            df, id, input_partition, npartitions, meta, parts_out, True
        )

    return assign_index_merge_transfer


class BlockwiseMerge(Merge, Blockwise):
    """Merge two dataframes with aligned partitions

    This operation will directly merge partition i of the
    left dataframe with partition i of the right dataframe.
    The two dataframes must be shuffled or partitioned
    by the merge key(s) before this operation is performed.
    Single-partition dataframes will always be broadcasted.

    See Also
    --------
    Merge
    """

    is_broadcast_join = False

    def _lower(self):
        return None

    def _broadcast_dep(self, dep: Expr):
        return dep.npartitions == 1

    def _task(self, index: int):
        return (
            apply,
            M.merge,
            [
                self._blockwise_arg(self.left, index),
                self._blockwise_arg(self.right, index),
            ],
            self.kwargs,
        )


class JoinRecursive(Expr):
    _parameters = ["frames", "how"]
    _defaults = {"right_index": True, "how": "outer"}

    @functools.cached_property
    def _meta(self):
        if len(self.frames) == 1:
            return self.frames[0]._meta
        else:
            return self.frames[0]._meta.join(
                [op._meta for op in self.frames[1:]],
            )

    def _divisions(self):
        npartitions = [frame.npartitions for frame in self.frames]
        return (None,) * (max(npartitions) + 1)

    def _lower(self):
        if self.how == "left":
            right = self._recursive_join(self.frames[1:])
            return Merge(
                self.frames[0],
                right,
                how=self.how,
                left_index=True,
                right_index=True,
            )

        return self._recursive_join(self.frames)

    def _recursive_join(self, frames):
        if len(frames) == 1:
            return frames[0]

        if len(frames) == 2:
            return Merge(
                frames[0],
                frames[1],
                how="outer",
                left_index=True,
                right_index=True,
            )

        midx = len(frames) // 2

        return self._recursive_join(
            [
                self._recursive_join(frames[:midx]),
                self._recursive_join(frames[midx:]),
            ],
        )