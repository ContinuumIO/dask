from __future__ import annotations

from itertools import count, product

import numpy as np

from dask.array.chunk import getitem
from dask.array.core import Array, unknown_chunk_message
from dask.base import tokenize
from dask.highlevelgraph import HighLevelGraph


def shuffle(x, indexer: list[list[int]], axis):
    if np.isnan(x.shape).any():
        raise ValueError(
            f"Shuffling only allowed with known chunk sizes. {unknown_chunk_message}"
        )
    token = tokenize(x, indexer, axis)
    out_name = f"shuffle-{token}"

    chunks, layer = _shuffle(x.chunks, indexer, axis, x.name, out_name)
    graph = HighLevelGraph.from_collections(out_name, layer, dependencies=[x])

    return Array(graph, out_name, chunks, meta=x)


def _shuffle(chunks, indexer, axis, in_name, out_name):
    if not isinstance(indexer, list) or not all(isinstance(i, list) for i in indexer):
        raise ValueError("indexer must be a list of lists of positional indices")

    if not axis <= len(chunks):
        raise ValueError(
            f"Axis {axis} is out of bounds for array with {len(chunks)} axes"
        )

    if max(map(max, indexer)) >= sum(chunks[axis]):
        raise IndexError(
            f"Indexer contains out of bounds index. Dimension only has {sum(chunks[axis])} elements."
        )

    average_chunk_size = int(sum(chunks[axis]) / len(chunks[axis]) * 1.25)

    # Figure out how many groups we can put into one chunk
    current_chunk, new_chunks = [], []
    for idx in indexer:
        if (
            len(current_chunk) + len(idx) > average_chunk_size
            and len(current_chunk) > 0
        ):
            new_chunks.append(current_chunk)
            current_chunk = idx.copy()
        else:
            current_chunk.extend(idx)
            if len(current_chunk) > average_chunk_size / 1.25:
                new_chunks.append(current_chunk)
                current_chunk = []
    if len(current_chunk) > 0:
        new_chunks.append(current_chunk)

    chunk_boundaries = np.cumsum(chunks[axis])

    # Get existing chunk tuple locations
    chunk_tuples = list(
        product(*(range(len(c)) for i, c in enumerate(chunks) if i != axis))
    )

    intermediates = dict()
    merges = dict()
    split_name = f"shuffle-split-{out_name.split('-')[-1]}"
    slices = [slice(None)] * len(chunks)
    split_name_suffixes = count()

    old_blocks = np.empty([len(c) for c in chunks], dtype="O")
    for old_index in np.ndindex(old_blocks.shape):
        old_blocks[old_index] = (in_name,) + old_index

    for new_chunk_idx, new_chunk_taker in enumerate(new_chunks):
        new_chunk_taker = np.array(new_chunk_taker)
        sorter = np.argsort(new_chunk_taker)
        sorted_array = new_chunk_taker[sorter]
        source_chunk_nr, taker_boundary = np.unique(
            np.searchsorted(chunk_boundaries, sorted_array, side="right"),
            return_index=True,
        )
        taker_boundary = taker_boundary.tolist()
        taker_boundary.append(len(new_chunk_taker))

        taker_cache = {}
        for chunk_tuple in chunk_tuples:
            merge_keys = []

            for c, b_start, b_end in zip(
                source_chunk_nr, taker_boundary[:-1], taker_boundary[1:]
            ):
                # insert our axis chunk id into the chunk_tuple
                chunk_key = convert_key(chunk_tuple, c, axis)
                name = (split_name, next(split_name_suffixes))
                this_slice = slices.copy()

                # Cache the takers to allow de-duplication when serializing
                # Ugly!
                if c in taker_cache:
                    this_slice[axis] = taker_cache[c]
                else:
                    this_slice[axis] = sorted_array[b_start:b_end] - (
                        chunk_boundaries[c - 1] if c > 0 else 0
                    )
                    if len(source_chunk_nr) == 1:
                        this_slice[axis] = this_slice[axis][np.argsort(sorter)]
                    taker_cache[c] = this_slice[axis]

                intermediates[name] = getitem, old_blocks[chunk_key], tuple(this_slice)
                merge_keys.append(name)

            merge_suffix = convert_key(chunk_tuple, new_chunk_idx, axis)
            if len(merge_keys) > 1:
                merges[(out_name,) + merge_suffix] = (
                    concatenate_arrays,
                    merge_keys,
                    sorter,
                    axis,
                )
            elif len(merge_keys) == 1:
                merges[(out_name,) + merge_suffix] = intermediates.pop(merge_keys[0])
            else:
                raise NotImplementedError

    output_chunks = []
    for i, c in enumerate(chunks):
        if i == axis:
            output_chunks.append(tuple(map(len, new_chunks)))
        else:
            output_chunks.append(c)

    layer = {**merges, **intermediates}
    return tuple(output_chunks), layer


def concatenate_arrays(arrs, sorter, axis):
    return np.take(np.concatenate(arrs, axis=axis), np.argsort(sorter), axis=axis)


def convert_key(key, chunk, axis):
    key = list(key)
    key.insert(axis, chunk)
    return tuple(key)
