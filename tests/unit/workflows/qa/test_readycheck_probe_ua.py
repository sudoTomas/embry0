"""The in-sandbox ready_check probe must send a browser-like User-Agent, not
urllib's default. Cloudflare / WAF bot rules 403 the default 'Python-urllib'
UA, so a deployed target fronted by Cloudflare would fail the liveness gate
even when the app is up (EMB-33 pilot job-90001a9ac4eb: 193 probes all 403).
"""

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from embry0.workflows.qa.boot import _PROBE_SCRIPT


def _run_probe_against_local_server():
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["user_agent"] = self.headers.get("User-Agent", "")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass  # silence

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.handle_request)
    t.start()

    out = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT, f"http://127.0.0.1:{port}/"],
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    t.join(timeout=5)
    server.server_close()
    return captured.get("user_agent", ""), out


def test_probe_sends_browser_user_agent_not_default_urllib():
    ua, out = _run_probe_against_local_server()
    assert out.startswith("STATUS=200"), out
    # The bug: urllib's default UA is 'Python-urllib/<ver>' — bot-blocked.
    assert not ua.startswith("Python-urllib"), f"probe still sends default urllib UA: {ua!r}"
    # The fix: a browser-like UA that WAF/bot rules pass.
    assert "Mozilla/" in ua, f"probe UA is not browser-like: {ua!r}"
