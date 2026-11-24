from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import load_settings
from app.contracts import ErrorDetail, ErrorResponse
from app.storage import create_storage_engine


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    engine = create_storage_engine(settings.database_url)
    application.state.settings = settings
    application.state.engine = engine
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(title="Keitaro Wrapper", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request, _exception: RequestValidationError
) -> JSONResponse:
    error = ErrorResponse(
        error=ErrorDetail(code="VALIDATION_ERROR", message="Проверьте данные запроса.")
    )
    return JSONResponse(status_code=422, content=error.as_payload())
