import json
import traceback
from contextlib import closing

import httpx
import pytest

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError

BASE_URL = "https://keitaro.test"
TEST_API_KEY = "synthetic-write-key"
CAMPAIGN = {
    "name": "test_demo_campaign",
    "alias": "demo_campaign",
    "domain_id": 1,
    "group_id": 2,
    "traffic_source_id": 3,
    "type": "position",
    "state": "disabled",
}
REDIRECT = {
    "campaign_id": 24,
    "name": "Country redirect",
    "type": "forced",
    "position": 1,
    "schema": "redirect",
    "state": "active",
    "action_type": "http",
    "action_payload": "https://google.com",
    "filters": [{"name": "country", "mode": "accept", "payload": ["IT"]}],
}
OFFERS = [
    {"offer_id": 17, "share": 50, "state": "active"},
    {"offer_id": 18, "share": 50, "state": "active"},
]


@pytest.fixture
def settings():
    return Settings(
        keitaro_base_url=BASE_URL,
        keitaro_api_key=TEST_API_KEY,
        keitaro_test_prefix="test_demo_",
        keitaro_domain_id=1,
        keitaro_group_id=2,
        keitaro_traffic_source_id=3,
        database_url="sqlite:///unused.db",
    )


@pytest.mark.parametrize(
    ("operation", "args", "method", "path", "payload", "status", "result"),
    [
        ("create_campaign", (CAMPAIGN,), "POST", "campaigns", CAMPAIGN, 200, {"id": 24}),
        ("create_stream", (REDIRECT,), "POST", "streams", REDIRECT, 200, {"id": 31}),
        ("set_stream_offers", (32, OFFERS), "PUT", "streams/32", {"offers": OFFERS}, 204, None),
        ("enable_campaign", (24,), "POST", "campaigns/24/enable", None, 200, None),
    ],
)
def test_write_uses_verified_method_and_request_body(
    settings, operation, args, method, path, payload, status, result
):
    requests = []

    def respond(request):
        requests.append(request)
        if result is None:
            return httpx.Response(status)
        return httpx.Response(status, json=result)

    with closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client:
        assert getattr(client, operation)(*args) == result

    [request] = requests
    assert request.method == method
    assert request.url == f"{BASE_URL}/admin_api/v1/{path}"
    assert request.headers["Api-Key"] == TEST_API_KEY
    assert request.headers["Accept"] == "application/json"
    if payload is None:
        assert request.content == b""
    else:
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == payload


@pytest.mark.parametrize(
    ("operation", "payload"),
    [("create_campaign", CAMPAIGN), ("create_stream", REDIRECT)],
)
@pytest.mark.parametrize("content", [b"", b"[]", b"["])
def test_create_requires_object_response(settings, operation, payload, content):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    with (
        closing(KeitaroClient(settings, transport=transport)) as client,
        pytest.raises(KeitaroError) as error,
    ):
        getattr(client, operation)(payload)

    assert error.value.kind == "invalid_response"


def test_write_does_not_forward_redirect(settings):
    hosts = []

    def respond(request):
        hosts.append(request.url.host)
        if request.url.host == "keitaro.test":
            return httpx.Response(
                307,
                headers={"Location": "https://other.test/write"},
                json={"detail": TEST_API_KEY},
            )
        return httpx.Response(200)

    with (
        closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client,
        pytest.raises(KeitaroError) as error,
    ):
        client.set_stream_offers(32, OFFERS)

    assert hosts == ["keitaro.test"]
    assert error.value.kind == "http"
    assert error.value.status_code == 307
    assert TEST_API_KEY not in str(error.value)


def test_write_timeout_is_sanitized_and_never_retried(settings):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.WriteTimeout(TEST_API_KEY, request=request)
        return httpx.Response(200, json={"id": 24})

    with (
        closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client,
        pytest.raises(KeitaroError) as error,
    ):
        client.create_campaign(CAMPAIGN)

    assert len(requests) == 1
    assert error.value.kind == "transport"
    assert TEST_API_KEY not in "".join(traceback.format_exception(error.value))
