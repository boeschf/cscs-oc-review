"""Deny production urllib access throughout the tooling test package.

The HTTP transport fixture explicitly replaces the opener with a guarded
loopback client. All other tests must inject their GitHub API implementation.
"""

import urllib.request

import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Reject accidental HTTP clients; the loopback fixture must opt in explicitly."""
    def forbidden(*args, **kwargs):
        pytest.fail("tooling tests must not access the network")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
