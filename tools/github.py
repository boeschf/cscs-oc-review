"""Shared authenticated GitHub JSON and release-asset upload transport.

API calls never follow redirects. Public asset downloads use a separate,
unauthenticated client so redirect targets cannot receive the repository token.
"""

import json
import os
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects rather than forwarding authenticated requests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirects are not allowed")


def github_api(path, payload=None):
    """GET JSON or POST JSON/asset bytes using GH_TOKEN and fixed GitHub origins.

    Paths must be API-relative. Binary payloads go unchanged to uploads.github.com;
    all JSON requests go to api.github.com. HTTP and decoding errors propagate.
    """
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("GitHub API path must start with a single slash")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required")
    if isinstance(payload, bytes):
        origin = "https://uploads.github.com"
        data = payload
        content_type = "application/octet-stream"
    else:
        origin = "https://api.github.com"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    request = urllib.request.Request(
        origin + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "cscs-oc-review-release",
                 "Content-Type": content_type},
        data=data,
        method="GET" if payload is None else "POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        return json.load(response)
