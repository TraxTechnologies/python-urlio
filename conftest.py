
import pytest


def pytest_configure(config):
    # register your new marker to avoid warnings
    config.addinivalue_line(
        "markers",
        "skipif_network: skip test if --network is not specified"
    )

def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", default=False,
            help="Run tests known to take a long time")
    parser.addoption("--network", action="store_true", default=False,
            help="Run tests that require network access")
