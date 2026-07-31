import os

from fastapi import FastAPI

app = FastAPI(title="devops-task-app")

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
