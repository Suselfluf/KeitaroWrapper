import logging
import traceback
from contextlib import closing

import httpx
import pytest

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError

BASE_URL = "https://keitaro.test/tracker"
TEST_API_KEY = "synthetic-keitaro-test-key"


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
    ("method", "args", "path", "query", "payload"),
    [
        ("get_offer", (17,), "offers/17", "", {"id": 17, "name": "Demo offer"}),
        ("list_domains", (), "domains", "", [{"id": 1}]),
        ("list_campaign_groups", (), "groups", "type=campaigns", [{"id": 2}]),
        ("list_traffic_sources", (), "traffic_sources", "", []),
        ("get_campaign", (24,), "campaigns/24", "", {"id": 24, "name": "test_demo_campaign"}),
        (
            "list_campaign_streams",
            (24,),
            "campaigns/24/streams",
            "",
            [{"id": 31}, {"id": 32, "offers": [{"share": 25}, {"share": 75}]}],
        ),
        ("get_stream", (31,), "streams/31", "", {"id": 31}),
    ],
)
def test_reads_send_expected_request_and_preserve_json(
    settings, method, args, path, query, payload
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    with closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client:
        assert getattr(client, method)(*args) == payload
        assert TEST_API_KEY not in repr(client)

    [request] = requests
    assert request.method == "GET"
    assert request.url == f"{BASE_URL}/admin_api/v1/{path}" + (f"?{query}" if query else "")
    assert request.headers["Api-Key"] == TEST_API_KEY
    assert request.headers["Accept"] == "application/json"
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }


def test_redirect_does_not_forward_api_key(settings):
    requested_hosts = []

    def respond(request):
        requested_hosts.append(request.url.host)
        if request.url.host == "keitaro.test":
            return httpx.Response(302, headers={"Location": "https://other.test/observe"})
        return httpx.Response(200, json={"id": 17})

    with (
        closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client,
        pytest.raises(KeitaroError) as result,
    ):
        client.get_offer(17)

    assert requested_hosts == ["keitaro.test"]
    assert result.value.kind == "http"
    assert result.value.status_code == 302


@pytest.mark.parametrize("status_code", [401, 404, 502])
def test_http_failure_exposes_only_safe_status(settings, status_code, caplog):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status_code, json={"detail": f"raw body {TEST_API_KEY}"})
    )
    with (
        caplog.at_level(logging.DEBUG),
        closing(KeitaroClient(settings, transport=transport)) as client,
        pytest.raises(KeitaroError) as result,
    ):
        client.get_offer(17)

    assert result.value.kind == "http"
    assert result.value.status_code == status_code
    assert str(result.value) == f"Keitaro вернул HTTP {status_code}."
    assert TEST_API_KEY not in caplog.text


def test_timeout_does_not_expose_api_key(settings, caplog):
    def respond(request):
        raise httpx.ReadTimeout(
            f"internal details: {request.headers['Api-Key']}", request=request
        )

    with (
        caplog.at_level(logging.DEBUG),
        closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client,
        pytest.raises(KeitaroError) as result,
    ):
        client.get_offer(17)

    assert result.value.kind == "transport"
    assert result.value.status_code is None
    assert TEST_API_KEY not in str(result.value)
    assert TEST_API_KEY not in "".join(traceback.format_exception(result.value))
    assert TEST_API_KEY not in caplog.text


@pytest.mark.parametrize(
    ("method", "args", "body"),
    [
        ("get_offer", (17,), b"["),
        ("get_offer", (17,), b"[]"),
        ("get_offer", (17,), b"null"),
        ("list_domains", (), b"{}"),
        ("list_domains", (), b"[1]"),
    ],
)
def test_success_status_requires_usable_json(settings, method, args, body):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    with (
        closing(KeitaroClient(settings, transport=transport)) as client,
        pytest.raises(KeitaroError) as result,
    ):
        getattr(client, method)(*args)

    assert result.value.kind == "invalid_response"
    assert result.value.status_code is None


@pytest.mark.parametrize("invalid_id", [0, -1, True, "../domains"])
def test_invalid_ids_do_not_make_a_request(settings, invalid_id):
    def respond(_):
        pytest.fail("Отправлен запрос с некорректным ID.")

    with (
        closing(KeitaroClient(settings, transport=httpx.MockTransport(respond))) as client,
        pytest.raises(ValueError, match="положительным целым"),
    ):
        client.get_offer(invalid_id)
