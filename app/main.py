"""FastAPI application entrypoint.

Exception handlers are overridden so that *every* response - success or failure
- uses the {"data": ..., "error": ...} envelope the brief requires. FastAPI's
defaults would otherwise emit a bare {"detail": ...} on errors.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import init_db
from app.routers import patients, vapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Patient registration API ready")
    yield


app = FastAPI(
    title="Voice AI Patient Registration API",
    description=(
        "REST API backing a Vapi voice agent that registers patients over the "
        "phone. The voice agent and API share one service layer."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(patients.router)
app.include_router(vapi.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": {"message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """422 with per-field detail, so an API client sees what the agent sees."""
    fields = {}
    for err in exc.errors():
        # loc is ("body", "<field>") - drop the container segment.
        loc = [str(p) for p in err["loc"] if p not in ("body", "query")]
        key = ".".join(loc) or "_"
        msg = err.get("msg", "is invalid")
        for prefix in ("Value error, ", "Assertion failed, "):
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
        fields[key] = msg
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "data": None,
            "error": {"message": "Validation failed.", "fields": fields},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"data": None, "error": {"message": "Internal server error."}},
    )


@app.get("/health", tags=["meta"], summary="Liveness probe")
def health():
    """Also the endpoint an uptime pinger hits to keep a free dyno warm."""
    return {"data": {"status": "ok"}, "error": None}


@app.get("/", tags=["meta"], include_in_schema=False)
def root():
    return {
        "data": {
            "service": "Voice AI Patient Registration API",
            "docs": "/docs",
            "endpoints": ["/patients", "/vapi/tools", "/health"],
        },
        "error": None,
    }
