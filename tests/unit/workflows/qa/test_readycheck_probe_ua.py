"""The in-sandbox ready_check probe must send a browser-like User-Agent, not
urllib's default. Cloudflare / WAF bot rules 403 the default 'Python-urllib'
UA, so a deployed target fronted by Cloudflare would fail the liveness gate
even when the app is up (EMB-33 pilot job-90001a9ac4eb: 193 probes all 403).
"""

import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from embry0.workflows.qa.boot import _PROBE_SCRIPT

_PROBE_PATH = "/ready-check"


def _run_probe_against_local_server():
    """The User-Agent the probe sent, and the probe's output.

    Any process on the host can reach the server's port, so only a request for
    the probe's path is taken for the probe's; any other is answered the same
    way and not recorded (#221).
    """
    probes: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == _PROBE_PATH:
                probes.append(self.headers.get("User-Agent", ""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass  # silence

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        out = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT, f"http://127.0.0.1:{port}{_PROBE_PATH}"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
    return (probes[-1] if probes else ""), out


def test_probe_sends_browser_user_agent_not_default_urllib():
    ua, out = _run_probe_against_local_server()
    assert out.startswith("STATUS=200"), out
    # The bug: urllib's default UA is 'Python-urllib/<ver>' — bot-blocked.
    assert not ua.startswith("Python-urllib"), f"probe still sends default urllib UA: {ua!r}"
    # The fix: a browser-like UA that WAF/bot rules pass.
    assert "Mozilla/" in ua, f"probe UA is not browser-like: {ua!r}"


def test_a_stray_request_is_not_taken_for_the_probe(monkeypatch):
    """Any process on the host can reach the local server's port. The server
    once answered one request, whichever came first, and reported its
    User-Agent: a stray's urllib default failed the test above, and a stray
    browser's would pass it whatever the probe sent (#221)."""
    run = subprocess.run

    def a_stray_then_the_probe(argv, **kwargs):
        target = urllib.parse.urlsplit(argv[-1])
        stray = urllib.request.Request(f"http://{target.netloc}/", headers={"User-Agent": "Python-urllib/3.12"})
        with urllib.request.urlopen(stray, timeout=5) as resp:
            assert resp.status == 200, "the stray request did not reach the server"
        return run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", a_stray_then_the_probe)
    ua, out = _run_probe_against_local_server()
    assert "Mozilla/" in ua, f"the stray's User-Agent was taken for the probe's: {ua!r}"
    assert out.startswith("STATUS=200"), f"the probe did not get through after a stray request: {out!r}"
