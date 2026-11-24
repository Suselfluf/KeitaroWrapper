from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.campaigns import CampaignCreationError, create_campaign
from app.config import load_settings
from app.contracts import CreateCampaign, CreatedCampaign, ErrorCode, ErrorDetail, ErrorResponse
from app.keitaro import KeitaroClient
from app.storage import create_storage_engine

_APP_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=_APP_DIR / "templates")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    engine = create_storage_engine(settings.database_url)
    try:
        client = KeitaroClient(settings)
        application.state.settings = settings
        application.state.engine = engine
        application.state.keitaro_client = client
        try:
            yield
        finally:
            client.close()
    finally:
        engine.dispose()


app = FastAPI(title="Keitaro Wrapper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_APP_DIR / "static"), name="static")


def get_keitaro_client(request: Request) -> KeitaroClient:
    return request.app.state.keitaro_client


def error_response(
    status: int, code: ErrorCode, message: str, campaign_id: int | None = None
) -> JSONResponse:
    error = ErrorResponse(
        error=ErrorDetail(code=code, message=message, campaign_id=campaign_id)
    )
    return JSONResponse(status_code=status, content=error.as_payload())


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request, _exception: RequestValidationError
) -> JSONResponse:
    return error_response(422, "VALIDATION_ERROR", "Проверьте данные запроса.")


@app.middleware("http")
async def restrict_api_writes(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(
        "/api/"
    ):
        provided_origin = request.headers.get("origin")
        origin = str(request.base_url).rstrip("/")
        if (
            request.headers.get("sec-fetch-site") == "cross-site"
            or provided_origin is not None
            and provided_origin != origin
        ):
            return error_response(403, "VALIDATION_ERROR", "Запрос с другого сайта отклонён.")
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    settings = request.app.state.settings
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "test_prefix": settings.keitaro_test_prefix,
            "domain_id": settings.keitaro_domain_id,
            "group_id": settings.keitaro_group_id,
            "traffic_source_id": settings.keitaro_traffic_source_id,
        },
    )


@app.post(
    "/api/campaigns",
    status_code=201,
    responses={status: {"model": ErrorResponse} for status in (403, 404, 422, 502)},
)
def create_campaign_route(
    body: CreateCampaign,
    request: Request,
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> CreatedCampaign:
    return create_campaign(request.app.state.settings, client, body)


@app.exception_handler(CampaignCreationError)
async def campaign_creation_error(
    _request: Request, error: CampaignCreationError
) -> JSONResponse:
    return error_response(error.status, error.code, str(error), error.campaign_id)
