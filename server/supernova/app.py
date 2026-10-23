"""HTTP gateway and game world the patched client talks to. See docs/protocol.md.

Run from the server/ folder:  python -m uvicorn supernova.app:app --host 0.0.0.0 --port 8000

The client's response key is read from an original APK: set SUPERNOVA_APK, or put the APK
into the repository's apk/ folder.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import crypto

# Oldest client version we accept, and the resource version we report. The client only
# downloads updated files when our resource version is newer than its own (header "resv"),
# so reporting the shipped version (6507 in 1.9.36) means "nothing to update".
LOWEST_CLIENT_VERSION = "1.9.36"
RESOURCE_VERSION = 6507

WORLD_ID = 1
WORLD_NAME = "Supernova"

log = logging.getLogger("supernova")
app = FastAPI(title="Project Supernova")


def find_client_key() -> bytes:
    apk_dir = Path(__file__).resolve().parents[2] / "apk"
    candidates = [Path(os.environ["SUPERNOVA_APK"])] if "SUPERNOVA_APK" in os.environ else sorted(apk_dir.glob("*.apk"))
    for apk in candidates:
        try:
            return crypto.load_key(apk)
        except (ValueError, OSError) as error:
            log.warning("%s", error)
    raise RuntimeError("No original APK found. Set SUPERNOVA_APK or put the APK into apk/.")


CLIENT_KEY = find_client_key()


@app.middleware("http")
async def collapse_slashes(request: Request, call_next):
    # Some client requests are built as gateway + "/servers/...", giving "//servers/...".
    path = request.scope["path"]
    while "//" in path:
        path = path.replace("//", "/")
    request.scope["path"] = path
    return await call_next(request)


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def encrypted_json(content: dict, status_code: int = 200) -> Response:
    """Response for requests the client sends in mode "normal"."""
    body = crypto.encrypt(CLIENT_KEY, json.dumps(content, separators=(",", ":")))
    return Response(body, status_code=status_code, media_type="application/json")


async def log_unhandled(request: Request) -> None:
    body = await request.body()
    log.warning(
        "UNHANDLED %s %s query=%s body=%s headers=%s",
        request.method,
        request.url.path,
        dict(request.query_params),
        body[:2000].decode("utf-8", "replace"),
        {k: v for k, v in request.headers.items() if k.lower() not in ("host", "connection")},
    )


# --- Gateway (plain JSON) ---------------------------------------------------------------


@app.get("/servers/auto_update")
def auto_update(request: Request):
    return {"url": f"{base_url(request)}/update", "lowestv": LOWEST_CLIENT_VERSION}


@app.get("/update/ver.txt")
def update_version():
    return PlainTextResponse(f"tag\n{RESOURCE_VERSION}\n{LOWEST_CLIENT_VERSION}\nsupernova")


@app.get("/servers/track_quest")
def track_quest():
    """Client progress tracking (launch, update finished, ...). Nothing to do."""
    return {}


@app.get("/servers.json")
def server_list(request: Request):
    return {
        "server_list": [
            {
                "server_id": WORLD_ID,
                "server_name": WORLD_NAME,
                "server_root_url": f"{base_url(request)}/world/{WORLD_ID}/",
                "language": "all",
                "busy": 0,
                "is_local": False,
                "status": "new",
                "is_recommended": True,
            }
        ]
    }


# --- Game world (encrypted JSON) ---------------------------------------------------------


@app.api_route("/world/{world_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def world_not_implemented(world_id: int, path: str, request: Request):
    await log_unhandled(request)
    return encrypted_json({"message": f"not implemented: {path}"}, status_code=404)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def not_implemented(path: str, request: Request):
    """Log everything the client asks for that we do not handle yet."""
    await log_unhandled(request)
    return JSONResponse({"message": f"not implemented: /{path}"}, status_code=404)
