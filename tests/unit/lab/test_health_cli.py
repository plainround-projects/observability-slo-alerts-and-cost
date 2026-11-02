import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from slo_lab.lab import health_cli


def test_every_part_is_reported_down_when_nothing_listens():
    results = health_cli.run_checks({"SERVICE_HOST_PORT": "9", "GATEWAY_HEALTH_HOST_PORT": "9"})
    assert len(results) == 9
    assert not any(ok for _, ok in results[:2])


def test_one_answering_part_is_reported_up_and_the_exit_code_is_1_while_others_are_down(monkeypatch, capsys):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("SERVICE_HOST_PORT", str(server.server_address[1]))
        monkeypatch.setenv("GATEWAY_HEALTH_HOST_PORT", "9")
        assert health_cli.main() == 1
    finally:
        server.shutdown()
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "ok   checkout /healthz"
    assert printed[1] == "FAIL gateway health_check"
