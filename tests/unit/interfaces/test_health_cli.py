import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from slo_lab.interfaces.health_cli import main


def _server(status):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_exit_code_is_0_for_a_200_and_1_for_anything_else():
    healthy, broken = _server(200), _server(503)
    try:
        assert main(["--url", f"http://127.0.0.1:{healthy.server_address[1]}/healthz"]) == 0
        assert main(["--url", f"http://127.0.0.1:{broken.server_address[1]}/healthz"]) == 1
    finally:
        healthy.shutdown()
        broken.shutdown()


def test_exit_code_is_1_when_nothing_listens():
    assert main(["--url", "http://127.0.0.1:9/healthz", "--timeout", "0.5"]) == 1
