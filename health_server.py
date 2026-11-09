"""Minimal HTTP health endpoint for hosting platforms that require an open PORT."""

from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

logger = logging.getLogger("treinamento_ff.health")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in ("/", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found\n")
            return
        body = b"EVOM NEXUS is running\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logger.info("HTTP health: " + format, *args)


def start_health_server() -> ThreadingHTTPServer:
    """Start a small HTTP server on 0.0.0.0:$PORT in a daemon thread."""
    raw_port = os.getenv("PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"PORT inválida: {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"PORT fora do intervalo válido: {port}")

    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, name="http-health-server", daemon=True)
    thread.start()
    logger.info("Servidor HTTP de saúde ouvindo em 0.0.0.0:%s", port)
    return server
