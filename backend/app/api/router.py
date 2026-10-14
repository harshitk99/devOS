from fastapi import APIRouter

from app.api import audit, diagnostics, executions, files, monitor, plans, projects, recovery, verification, ws

api_router = APIRouter(prefix="/api")
for module in (projects, diagnostics, files, plans, executions, verification, recovery, audit, monitor):
    api_router.include_router(module.router)

ws_router = ws.router
