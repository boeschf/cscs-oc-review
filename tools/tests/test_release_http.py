"""Exercise authenticated urllib requests over loopback without external traffic."""

import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import urllib.request

import pytest


TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cscs_release_http_tests", TOOLS / "release.py", submodule_search_locations=[str(TOOLS)])
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)

BUILD_OPENER = urllib.request.build_opener
TOKEN = "test-only-not-a-real-github-token"
ASSET_PATH = "/repos/eth-cscs/cscs-oc-review/releases/123/assets?name=cscs-review.yml"
JSON_PATH = "/repos/eth-cscs/cscs-oc-review/releases"


@pytest.fixture
def loopback_github(monkeypatch):
    """Keep urllib serialization/redirect handling; route HTTPS sockets to localhost."""
    received = []
    responses = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append({
                "method": self.command, "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            })
            status, headers = responses.get(self.path, (200, {}))
            response = b'{"accepted": true}'
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    address = server.server_address
    connect = socket.create_connection

    def local_connection(target, *args, **kwargs):
        if target != address:
            pytest.fail(f"external connection forbidden: {target!r}")
        return connect(target, *args, **kwargs)

    class LoopbackHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(
                lambda host, timeout: http.client.HTTPConnection(*address, timeout=timeout),
                request,
            )

    def opener(*handlers):
        return BUILD_OPENER(urllib.request.ProxyHandler({}), LoopbackHTTPS(), *handlers)

    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setattr(socket, "create_connection", local_connection)
    monkeypatch.setattr(release.urllib.request, "build_opener", opener)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield received, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("body", [b"", bytes(range(256)) + b"\r\ncaller: \x00\xff\n"])
def test_binary_upload_reaches_upload_origin_without_byte_corruption(loopback_github, body):
    received, _ = loopback_github

    assert release.github_api(ASSET_PATH, body) == {"accepted": True}

    assert len(received) == 1
    request = received[0]
    assert request["method"] == "POST"
    assert request["path"] == ASSET_PATH
    assert request["body"] == body
    assert request["headers"]["host"] == "uploads.github.com"
    assert request["headers"]["authorization"] == "Bearer " + TOKEN
    assert request["headers"]["content-type"] == "application/octet-stream"
    assert int(request["headers"]["content-length"]) == len(body)


@pytest.mark.parametrize("payload", [None, {"draft": True, "body": "Release notes: café\n"}])
def test_json_requests_remain_on_api_origin(loopback_github, payload):
    received, _ = loopback_github

    assert release.github_api(JSON_PATH, payload) == {"accepted": True}

    assert len(received) == 1
    request = received[0]
    assert request["method"] == ("GET" if payload is None else "POST")
    assert request["path"] == JSON_PATH
    assert request["headers"]["host"] == "api.github.com"
    assert request["headers"]["authorization"] == "Bearer " + TOKEN
    assert request["headers"]["content-type"] == "application/json"
    if payload is None:
        assert request["body"] == b""
    else:
        assert json.loads(request["body"]) == payload


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("destination", [
    "https://api.github.com/redirect-target",
    "https://credential-thief.invalid/redirect-target",
])
@pytest.mark.parametrize("payload", [{"draft": True}, b"caller bytes\x00\xff"])
def test_authenticated_redirects_never_reach_the_destination(loopback_github, status, destination, payload):
    received, responses = loopback_github
    path = ASSET_PATH if isinstance(payload, bytes) else JSON_PATH
    responses[path] = (status, {"Location": destination})

    with pytest.raises(ValueError, match="redirects are not allowed"):
        release.github_api(path, payload)

    # A redirect would reach this same loopback server even for the hostile host.
    # Observing only the initial request proves no credentials reached the target.
    assert len(received) == 1
    assert received[0]["path"] == path
    assert received[0]["headers"]["authorization"] == "Bearer " + TOKEN
