# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import (
    APIRouter, Request
)
from backend.utilities.storage.services import (
    load_service_config, save_service_config
)

service_router = APIRouter(tags=["ServiceConfig"])


@service_router.get(path="/api/service-config", include_in_schema=False)
async def api_service_config_load() -> dict:
    return {
        "ok"   : True,
        "data" : load_service_config()
    }


@service_router.put(path="/api/service-config", include_in_schema=False)
async def api_service_config_save(request: Request) -> dict:
    payload = await request.json()
    return {
        "ok"   : True,
        "data" : save_service_config(payload)
    }


if __name__ == '__main__':
    pass
