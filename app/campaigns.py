from uuid import uuid4

from app.config import Settings
from app.contracts import CreateCampaign, CreatedCampaign, ErrorCode
from app.keitaro import JsonObject, KeitaroClient, KeitaroError

_GOOGLE_URL = "https://google.com"


class CampaignCreationError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        campaign_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.campaign_id = campaign_id


def _entity_id(row: JsonObject) -> int:
    value = row.get("id")
    if type(value) is not int or value <= 0:
        raise KeitaroError("invalid_response")
    return value


def _matches(row: JsonObject, expected: JsonObject) -> bool:
    return all(row.get(key) == value for key, value in expected.items())


def _definite_rejection(error: KeitaroError) -> bool:
    return (
        error.kind == "http"
        and error.status_code is not None
        and 400 <= error.status_code < 500
    )


def _preflight(settings: Settings, client: KeitaroClient, request: CreateCampaign) -> None:
    prefix = settings.keitaro_test_prefix
    if not request.name.startswith(prefix) or len(request.name) == len(prefix):
        raise CampaignCreationError(
            422, "VALIDATION_ERROR", f"Введите название с префиксом {prefix} и суффиксом."
        )

    try:
        offer = client.get_offer(request.offer_id)
    except KeitaroError as error:
        if error.status_code == 404:
            raise CampaignCreationError(
                404, "NOT_FOUND", f"Оффер № {request.offer_id} не найден."
            ) from None
        raise CampaignCreationError(502, "KEITARO_ERROR", str(error)) from None
    if offer.get("id") != request.offer_id:
        raise CampaignCreationError(502, "KEITARO_ERROR", "Keitaro вернул другой оффер.")
    if offer.get("state") != "active":
        raise CampaignCreationError(404, "NOT_FOUND", f"Оффер № {request.offer_id} недоступен.")

    try:
        catalogs = (
            (client.list_domains(), settings.keitaro_domain_id, "Домен"),
            (client.list_campaign_groups(), settings.keitaro_group_id, "Группа"),
            (client.list_traffic_sources(), settings.keitaro_traffic_source_id, "Источник"),
        )
    except KeitaroError as error:
        raise CampaignCreationError(502, "KEITARO_ERROR", str(error)) from None
    for rows, selected_id, label in catalogs:
        if not any(row.get("id") == selected_id for row in rows):
            raise CampaignCreationError(
                502,
                "KEITARO_ERROR",
                f"{label} № {selected_id} не найден в справочнике Keitaro.",
            )


def _verify_streams(
    client: KeitaroClient,
    campaign_id: int,
    stream_ids: tuple[int, int],
    country_code: str,
    offer_id: int,
) -> None:
    order = [
        (stream.get("id"), stream.get("position"), stream.get("type"))
        for stream in client.list_campaign_streams(campaign_id)
    ]
    if order != [(stream_ids[0], 1, "forced"), (stream_ids[1], 2, "default")]:
        raise KeitaroError("invalid_response")
    redirect, fallback = (client.get_stream(stream_id) for stream_id in stream_ids)
    if not _matches(
        redirect,
        {
            "id": stream_ids[0],
            "campaign_id": campaign_id,
            "position": 1,
            "type": "forced",
            "schema": "redirect",
            "state": "active",
            "action_type": "http",
            "action_payload": _GOOGLE_URL,
        },
    ) or not _matches(
        fallback,
        {
            "id": stream_ids[1],
            "campaign_id": campaign_id,
            "position": 2,
            "type": "default",
            "schema": "landings",
            "state": "active",
            "action_type": "http",
            "filters": [],
            "landings": [],
        },
    ):
        raise KeitaroError("invalid_response")

    filters = redirect.get("filters")
    offers = fallback.get("offers")
    if (
        not isinstance(filters, list)
        or len(filters) != 1
        or not isinstance(filters[0], dict)
        or not _matches(
            filters[0], {"name": "country", "mode": "accept", "payload": [country_code]}
        )
        or not isinstance(offers, list)
        or len(offers) != 1
        or not isinstance(offers[0], dict)
        or not _matches(offers[0], {"offer_id": offer_id, "share": 100, "state": "active"})
    ):
        raise KeitaroError("invalid_response")


def create_campaign(
    settings: Settings, client: KeitaroClient, request: CreateCampaign
) -> CreatedCampaign:
    _preflight(settings, client, request)
    settings_to_verify: JsonObject = {
        "name": request.name,
        "domain_id": settings.keitaro_domain_id,
        "group_id": settings.keitaro_group_id,
        "traffic_source_id": settings.keitaro_traffic_source_id,
        "type": "position",
    }
    try:
        created = client.create_campaign(
            {**settings_to_verify, "alias": f"kw_{uuid4().hex}", "state": "disabled"}
        )
        campaign_id = _entity_id(created)
    except KeitaroError as error:
        if _definite_rejection(error):
            raise CampaignCreationError(502, "KEITARO_ERROR", str(error)) from None
        raise CampaignCreationError(
            502,
            "CREATE_UNCONFIRMED",
            "Keitaro не подтвердил создание кампании. Проверьте кампанию перед повтором.",
        ) from None

    try:
        if not _matches(
            client.get_campaign(campaign_id),
            {"id": campaign_id, **settings_to_verify, "state": "disabled"},
        ):
            raise KeitaroError("invalid_response")
        redirect = client.create_stream(
            {
                "campaign_id": campaign_id,
                "name": "Country redirect",
                "type": "forced",
                "position": 1,
                "schema": "redirect",
                "state": "active",
                "action_type": "http",
                "action_payload": _GOOGLE_URL,
                "filters": [
                    {"name": "country", "mode": "accept", "payload": [request.country_code]}
                ],
            }
        )
        redirect_id = _entity_id(redirect)
        fallback = client.create_stream(
            {
                "campaign_id": campaign_id,
                "name": "Fallback offer",
                "type": "default",
                "position": 2,
                "schema": "landings",
                "state": "active",
                "action_type": "http",
                "filters": [],
                "landings": [],
                "offers": [{"offer_id": request.offer_id, "share": 100, "state": "active"}],
            }
        )
        stream_ids = (redirect_id, _entity_id(fallback))
        if stream_ids[0] == stream_ids[1]:
            raise KeitaroError("invalid_response")
        _verify_streams(client, campaign_id, stream_ids, request.country_code, request.offer_id)
    except KeitaroError as error:
        raise CampaignCreationError(
            502,
            "PARTIAL_CREATE",
            f"Кампания создана частично. Keitaro не подтвердил все настройки. {error}",
            campaign_id,
        ) from None

    try:
        client.enable_campaign(campaign_id)
    except KeitaroError as error:
        if _definite_rejection(error):
            raise CampaignCreationError(
                502,
                "PARTIAL_CREATE",
                f"Кампания и потоки созданы. Keitaro не подтвердил включение. {error}",
                campaign_id,
            ) from None
        raise _activation_unconfirmed(campaign_id) from None
    try:
        activated = client.get_campaign(campaign_id)
    except KeitaroError:
        raise _activation_unconfirmed(campaign_id) from None
    if not _matches(activated, {"id": campaign_id, **settings_to_verify, "state": "active"}):
        raise CampaignCreationError(
            502,
            "PARTIAL_CREATE",
            "Кампания и потоки созданы. Прочитанные настройки включённой кампании не совпали.",
            campaign_id,
        )

    return CreatedCampaign(campaign_id=campaign_id, name=request.name, stream_ids=stream_ids)


def _activation_unconfirmed(campaign_id: int) -> CampaignCreationError:
    return CampaignCreationError(
        502,
        "CREATE_UNCONFIRMED",
        "Keitaro не подтвердил включение кампании. Проверьте её перед повтором.",
        campaign_id,
    )
