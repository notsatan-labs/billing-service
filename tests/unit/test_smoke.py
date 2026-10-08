"""Scaffold smoke test — real API tests come later."""

from billing_meter import __version__


def test_package_version():
    assert __version__ == "0.1.0"
