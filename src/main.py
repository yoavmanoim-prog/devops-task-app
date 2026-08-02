import os

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="devops-task-app")

# Exposes /metrics: request counts, durations and in-flight requests, labelled
# by method, path and status. The cluster runs kube-prometheus-stack, but it
# only collected node and control-plane metrics - nothing scraped this app,
# because there was nothing to scrape. The chart's ServiceMonitor points here.
#
# excluded_handlers keeps the probe endpoints out of the metrics: kubelet hits
# /healthz and /readyz every few seconds, which would dominate the request
# counters and make the latency histograms describe the probes rather than real
# traffic. /metrics excludes itself for the same reason once Prometheus starts
# scraping it.
Instrumentator(
    excluded_handlers=["/healthz", "/readyz", "/metrics"],
).instrument(app).expose(app, include_in_schema=False)

# Path is overridable so tests can point it at a tmp dir instead of requiring
# a real Secret volume mount - defaults to where the gitops chart mounts the
# ExternalSecret-backed Secret when externalSecret.enabled.
SECRET_FILE_PATH = os.environ.get("SECRET_FILE_PATH", "/etc/secrets/message")


def _read_secret_message() -> str | None:
    try:
        with open(SECRET_FILE_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


@app.get("/")
def read_root() -> dict:
    return {
        "message": "hello from devops-task-app",
        "app_env": os.environ.get("APP_ENV"),
        "secret_message": _read_secret_message(),
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict:
    return {"status": "ok"}
