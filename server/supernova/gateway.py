"""Gateway endpoints: update check and world list. Plain JSON (client mode "notencrypt")."""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from . import config

# Oldest client version we accept, and the resource version we report. The client only
# downloads updated files when our resource version is newer than its own (header "resv"),
# so reporting the shipped version (6507 in 1.9.36) means "nothing to update".
LOWEST_CLIENT_VERSION = "1.9.36"
RESOURCE_VERSION = 6507

router = APIRouter()


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/servers/auto_update")
def auto_update(request: Request):
    return {"url": f"{base_url(request)}/update", "lowestv": LOWEST_CLIENT_VERSION}


@router.get("/update/ver.txt")
def update_version():
    return PlainTextResponse(f"tag\n{RESOURCE_VERSION}\n{LOWEST_CLIENT_VERSION}\nsupernova")


@router.get("/servers/track_quest")
def track_quest():
    """Client progress tracking (launch, update finished, ...). Nothing to do."""
    return {}


@router.get("/servers.json")
def server_list(request: Request):
    return {
        "server_list": [
            {
                "server_id": config.WORLD_ID,
                "server_name": config.WORLD_NAME,
                "server_root_url": f"{base_url(request)}/world/{config.WORLD_ID}/",
                "language": "all",
                "busy": 0,
                "is_local": False,
                "status": "new",
                "is_recommended": True,
            }
        ]
    }
