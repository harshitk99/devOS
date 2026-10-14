from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.api.router import api_router, ws_router
from app.core.db import Base, engine
from app.services.explorer import ExplorerError
from app.services.pipeline import PipelineError


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # prototype: no migrations yet
    yield


app = FastAPI(title="DevOS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)


@app.exception_handler(PipelineError)
async def pipeline_error_handler(_: Request, exc: PipelineError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ExplorerError)
async def explorer_error_handler(_: Request, exc: ExplorerError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
