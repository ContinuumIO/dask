import dask
import pytest


def test_mimesis():
    pytest.importorskip('mimesis')

    b = dask.datasets.make_people()
    assert b.take(5)

    assert b.take(3) == b.take(3)


def test_no_mimesis():
    try:
        import mimesis  # noqa: F401
    except ImportError:
        with pytest.raises(Exception) as info:
            dask.datasets.make_people()

        assert "pip install mimesis" in str(info.value)
