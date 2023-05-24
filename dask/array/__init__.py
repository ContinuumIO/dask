from __future__ import annotations

try:
    from dask.array import backends, fft, lib, linalg, ma, overlap, random
    from dask.array.blockwise import atop, blockwise
    from dask.array.chunk_types import register_chunk_type
    from dask.array.core import (
        Array,
        PerformanceWarning,
        asanyarray,
        asarray,
        block,
        broadcast_arrays,
        broadcast_to,
        concatenate,
        from_array,
        from_delayed,
        from_npy_stack,
        from_zarr,
        map_blocks,
        stack,
        store,
        to_hdf5,
        to_npy_stack,
        to_zarr,
        unify_chunks,
    )
    from dask.array.creation import (
        arange,
        diag,
        diagonal,
        empty_like,
        eye,
        fromfunction,
        full_like,
        indices,
        linspace,
        meshgrid,
        ones_like,
        pad,
        repeat,
        tile,
        tri,
        zeros_like,
    )
    from dask.array.gufunc import apply_gufunc, as_gufunc, gufunc
    from dask.array.numpy_compat import moveaxis, rollaxis
    from dask.array.optimization import optimize
    from dask.array.overlap import map_overlap
    from dask.array.percentile import percentile
    from dask.array.rechunk import rechunk
    from dask.array.reductions import (
        all,
        any,
        argmax,
        argmin,
        argtopk,
        cumprod,
        cumsum,
        max,
        mean,
        median,
        min,
        moment,
        nanargmax,
        nanargmin,
        nancumprod,
        nancumsum,
        nanmax,
        nanmean,
        nanmedian,
        nanmin,
        nanprod,
        nanstd,
        nansum,
        nanvar,
        prod,
        reduction,
        std,
        sum,
        topk,
        trace,
        var,
    )
    from dask.array.reshape import reshape
    from dask.array.routines import (
        allclose,
        append,
        apply_along_axis,
        apply_over_axes,
        argwhere,
        around,
        array,
        atleast_1d,
        atleast_2d,
        atleast_3d,
        average,
        bincount,
        choose,
        coarsen,
        compress,
        corrcoef,
        count_nonzero,
        cov,
        delete,
        diff,
        digitize,
        dot,
        dstack,
        ediff1d,
        einsum,
        expand_dims,
        extract,
        flatnonzero,
        flip,
        fliplr,
        flipud,
        gradient,
        histogram,
        histogram2d,
        histogramdd,
        hstack,
        insert,
        isclose,
        isin,
        isnull,
        matmul,
        ndim,
        nonzero,
        notnull,
        outer,
        piecewise,
        ptp,
        ravel,
        ravel_multi_index,
        result_type,
        roll,
        rot90,
        round,
        searchsorted,
        select,
        shape,
        squeeze,
        swapaxes,
        take,
        tensordot,
        transpose,
        tril,
        tril_indices,
        tril_indices_from,
        triu,
        triu_indices,
        triu_indices_from,
        union1d,
        unique,
        unravel_index,
        vdot,
        vstack,
        where,
    )
    from dask.array.tiledb_io import from_tiledb, to_tiledb
    from dask.array.ufunc import (
        abs,
        absolute,
        add,
        angle,
        arccos,
        arccosh,
        arcsin,
        arcsinh,
        arctan,
        arctan2,
        arctanh,
        bitwise_and,
        bitwise_not,
        bitwise_or,
        bitwise_xor,
        cbrt,
        ceil,
        clip,
        conj,
        copysign,
        cos,
        cosh,
        deg2rad,
        degrees,
        divide,
        divmod,
        equal,
        exp,
        exp2,
        expm1,
        fabs,
        fix,
        float_power,
        floor,
        floor_divide,
        fmax,
        fmin,
        fmod,
        frexp,
        frompyfunc,
        greater,
        greater_equal,
        hypot,
        i0,
        imag,
        invert,
        iscomplex,
        isfinite,
        isinf,
        isnan,
        isneginf,
        isposinf,
        isreal,
        ldexp,
        left_shift,
        less,
        less_equal,
        log,
        log1p,
        log2,
        log10,
        logaddexp,
        logaddexp2,
        logical_and,
        logical_not,
        logical_or,
        logical_xor,
        maximum,
        minimum,
        mod,
        modf,
        multiply,
        nan_to_num,
        negative,
        nextafter,
        not_equal,
        positive,
        power,
        rad2deg,
        radians,
        real,
        reciprocal,
        remainder,
        right_shift,
        rint,
        sign,
        signbit,
        sin,
        sinc,
        sinh,
        spacing,
        sqrt,
        square,
        subtract,
        tan,
        tanh,
        true_divide,
        trunc,
    )
    from dask.array.utils import assert_eq
    from dask.array.wrap import empty, full, ones, zeros
    from dask.base import compute

except ImportError as e:
    msg = (
        "Dask array requirements are not installed.\n\n"
        "Please either conda or pip install as follows:\n\n"
        "  conda install dask                 # either conda install\n"
        '  python -m pip install "dask[array]" --upgrade  # or python -m pip install'
    )
    raise ImportError(str(e) + "\n\n" + msg) from e
