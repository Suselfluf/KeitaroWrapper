from typing import Literal

import httpx

from app.config import Settings

KeitaroErrorKind = Literal["transport", "http", "invalid_response"]
JsonObject = dict[str, object]


class KeitaroError(RuntimeError):
    def __init__(self, kind: KeitaroErrorKind, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        if kind == "http":
            message = f"Keitaro вернул HTTP {status_code}."
        elif kind == "transport":
            message = "Не удалось подключиться к Keitaro."
        else:
            message = "Keitaro вернул ответ неожиданного формата."
        super().__init__(message)


class KeitaroClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._api_key = settings.keitaro_api_key
        self._http = httpx.Client(
            base_url=f"{settings.keitaro_base_url}/admin_api/v1/",
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def get_offer(self, offer_id: int) -> JsonObject:
        return self._get_object(f"offers/{_id_segment(offer_id)}")

    def list_domains(self) -> list[JsonObject]:
        return self._get_collection("domains")

    def list_campaign_groups(self) -> list[JsonObject]:
        return self._get_collection("groups", params={"type": "campaigns"})

    def list_traffic_sources(self) -> list[JsonObject]:
        return self._get_collection("traffic_sources")

    def get_campaign(self, campaign_id: int) -> JsonObject:
        return self._get_object(f"campaigns/{_id_segment(campaign_id)}")

    def list_campaign_streams(self, campaign_id: int) -> list[JsonObject]:
        return self._get_collection(f"campaigns/{_id_segment(campaign_id)}/streams")

    def get_stream(self, stream_id: int) -> JsonObject:
        return self._get_object(f"streams/{_id_segment(stream_id)}")

    def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> object:
        try:
            response = self._http.get(
                path,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Api-Key": self._api_key.get_secret_value(),
                },
            )
        except (httpx.RequestError, UnicodeError):
            raise KeitaroError("transport") from None
        if not response.is_success:
            raise KeitaroError("http", status_code=response.status_code)
        try:
            return response.json()
        except ValueError:
            raise KeitaroError("invalid_response") from None

    def _get_object(self, path: str) -> JsonObject:
        data = self._get_json(path)
        if not isinstance(data, dict):
            raise KeitaroError("invalid_response")
        return data

    def _get_collection(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> list[JsonObject]:
        data = self._get_json(path, params=params)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise KeitaroError("invalid_response")
        return data


def _id_segment(entity_id: int) -> int:
    if isinstance(entity_id, bool) or not isinstance(entity_id, int) or entity_id <= 0:
        raise ValueError("ID должен быть положительным целым числом.")
    return entity_id
