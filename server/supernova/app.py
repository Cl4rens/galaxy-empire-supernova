"""Project Supernova server. See docs/protocol.md.

Run from the server/ folder:  python -m uvicorn supernova.app:app --host 0.0.0.0 --port 8000

Settings come from environment variables, see supernova/config.py.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import config, crypto, db, fleets, fortresses, gateway, operations, world

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supernova")


def find_client_key() -> bytes:
    candidates = [Path(config.APK_PATH)] if config.APK_PATH else sorted(config.APK_DIR.glob("*.apk"))
    for apk in candidates:
        try:
            return crypto.load_key(apk)
        except (ValueError, OSError) as e:
            log.warning("%s", e)
    message = f"No original APK found. Set SUPERNOVA_APK or put the APK into {config.APK_DIR}."
    if config.REQUIRE_CLIENT_KEY:
        raise RuntimeError(message)
    log.error("%s Game world disabled.", message)
    return b""


world.CLIENT_KEY = find_client_key()

async def fleet_processor():
    """Completes fleet arrivals and returns every second, also for players who are offline."""
    while True:
        try:
            await asyncio.to_thread(fleets.process_due)
            await asyncio.to_thread(fortresses.process_due)
            await asyncio.to_thread(operations.housekeeping, time.time())
        except Exception:
            log.exception("fleet processing failed")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(fleet_processor())
    yield
    task.cancel()


app = FastAPI(title="Project Supernova", lifespan=lifespan)
app.include_router(gateway.router)
app.include_router(world.router)


@app.middleware("http")
async def require_client_key(request: Request, call_next):
    if not world.CLIENT_KEY and request.url.path.startswith("/world/"):
        return PlainTextResponse("Server is missing the client key", status_code=503)
    return await call_next(request)


@app.exception_handler(world.NotLoggedIn)
def not_logged_in(request: Request, exc: world.NotLoggedIn):
    return world.error("Please log in again", 401)


@app.exception_handler(db.Banned)
def banned(request: Request, exc: db.Banned):
    return world.error(str(exc), 403)


@app.get("/health")
def health():
    """For container health checks and monitoring."""
    return {"status": "ok", "players": operations.player_count()}


@app.middleware("http")
async def collapse_slashes(request: Request, call_next):
    # Some client requests are built as gateway + "/servers/...", giving "//servers/...".
    path = request.scope["path"]
    while "//" in path:
        path = path.replace("//", "/")
    request.scope["path"] = path
    return await call_next(request)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def not_implemented(path: str, request: Request):
    """Log everything the client asks for that we do not handle yet."""
    body = await request.body()
    log.warning("UNHANDLED %s /%s query=%s body=%s", request.method, path,
                dict(request.query_params), body[:2000].decode("utf-8", "replace"))
    return JSONResponse({"message": f"not implemented: /{path}"}, status_code=404)
