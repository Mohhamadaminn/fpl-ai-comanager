"""Gunicorn settings for production (used by the Dockerfile and docker-compose.prod.yml).

Threaded workers: a request that is waiting on Groq or the FPL API only occupies one
thread, so a few slow requests no longer freeze the whole API (3 sync workers could be
fully blocked by 3 slow requests).

Note: with the gthread worker the `timeout` setting only detects a hung worker process,
it does not cut off one slow request. How long a single request can take is therefore
bounded by the explicit timeouts on the outgoing calls (GROQ_TIMEOUT_SECONDS,
requests timeouts), not by this value.
"""
import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "90"))
graceful_timeout = 30
keepalive = 5
