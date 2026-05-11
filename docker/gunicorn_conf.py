"""Gunicorn configuration for GeneReview-Link production deployment."""

from __future__ import annotations

import os
from typing import Any

bind = f"0.0.0.0:{os.environ.get('GENEREVIEW_LINK_PORT', os.environ.get('PORT', '8000'))}"
backlog = 2048

workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50

timeout = 30
keepalive = 2
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True
enable_stdio_inheritance = True

proc_name = "genereview-link"

limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")
secure_scheme_headers = {
    "X-FORWARDED-PROTO": "https",
    "X-FORWARDED-SSL": "on",
}

preload_app = True
reuse_port = True

worker_tmp_dir = "/dev/shm"


def on_starting(server: Any) -> None:
    server.log.info("Starting GeneReview-Link server")


def on_reload(server: Any) -> None:
    server.log.info("Reloading GeneReview-Link server")


def worker_int(worker: Any) -> None:
    worker.log.info("Worker received INT or QUIT signal")


def post_fork(server: Any, worker: Any) -> None:
    server.log.info("Worker spawned (pid: %s)", worker.pid)


def post_worker_init(worker: Any) -> None:
    worker.log.info("Worker initialized (pid: %s)", worker.pid)


def worker_abort(worker: Any) -> None:
    worker.log.info("Worker aborted (pid: %s)", worker.pid)
