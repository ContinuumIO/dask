#!/usr/bin/env bash

set -e

if [[ $ARRAYEXPR == 'true' ]]; then
    export MARKERS="-m 'array_expr'"
else
    export MARKERS="-m 'array_expr'"
fi

if [[ $PARALLEL == 'true' ]]; then
    export XTRATESTARGS="-n4 $XTRATESTARGS"
fi

if [[ $COVERAGE == 'true' ]]; then
    export XTRATESTARGS="--cov=dask --cov-report=xml --junit-xml pytest.xml $XTRATESTARGS"
fi

echo "py.test $MARKERS dask --runslow $XTRATESTARGS"
py.test $MARKERS dask --runslow $XTRATESTARGS

set +e
