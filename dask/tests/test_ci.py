import importlib.metadata
import os

import pytest
from packaging.version import Version


@pytest.mark.xfail(reason="https://github.com/dask/dask/issues/9735", strict=False)
@pytest.mark.skipif(
    not os.environ.get("UPSTREAM_DEV", False),
    reason="Only check for dev packages in `upstream` CI build",
)
def test_upstream_packages_installed():
    # List of packages should match those specified in
    # `continuous_integration/scripts/install.sh`

    # FIXME: This test isn't sensative to projects that use git tags
    # to determine versions (e.g. versionseer) when installed
    # directly from GitHub as the latest `main` branch can sometimes
    # be pointing to a released version of the project.
    packages = [
        "bokeh",
        # "dask",
        # "distributed",
        # "fastparquet",
        # "fsspec",
        "numpy",
        "pandas",
        # "partd",
        "pyarrow",
        # "s3fs",
        "scipy",
        # "sparse",
        # "zarr",
        # "zict",
    ]
    for package in packages:
        v = Version(importlib.metadata.version(package))
        assert v.is_prerelease or v.local is not None, (package, str(v))
