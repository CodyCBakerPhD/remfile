"""Tests of the threads that remfile's requests are made in.

h5py holds a global lock while it reads from a file object, and needs the
requests to be made in the calling thread (see the README).  These use a
local HTTP server, so they need no network access.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import remfile

# Large enough (> 2 x 4 MB) for remfile to split a read across threads when
# allowed to
DATA = bytes(range(256)) * (40 * 1024)  # 10 MB


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        if rng:
            start_s, end_s = rng.split("=")[1].split("-")
            body, status = DATA[int(start_s):int(end_s) + 1], 206
        else:
            body, status = DATA, 200
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/data.bin"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def request_threads(monkeypatch):
    """The threads that the range requests are made in"""
    threads = []
    get = requests.Session.get

    def recording_get(self, *args, **kwargs):
        if "Range" in kwargs.get("headers", {}):
            threads.append(threading.current_thread())
        return get(self, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "get", recording_get)
    return threads


def test_requests_are_made_in_the_calling_thread_by_default(url, request_threads):
    f = remfile.File(url)
    assert f.read(len(DATA)) == DATA
    assert request_threads
    assert all(t is threading.current_thread() for t in request_threads)


def test_more_threads_fetch_large_ranges_in_other_threads(url, request_threads):
    f = remfile.File(url, _max_threads=3)
    assert f.read(len(DATA)) == DATA
    assert any(t is not threading.current_thread() for t in request_threads)
