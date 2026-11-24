import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.keitaro import KeitaroClient
from app.main import app, get_keitaro_client

CAMPAIGN_REQUEST = {"name": "  test_example_roma  ", "country_code": "it", "offer_id": 12}
CAMPAIGN_FIELDS = {
    "name": "test_example_roma",
    "domain_id": 31,
    "group_id": 32,
    "traffic_source_id": 33,
    "type": "position",
    "state": "disabled",
}


class FakeUpstream:
    def __init__(self):
        self.requests = []
        self.campaign = {}
        self.streams = {}
        self.bad_catalog = False
        self.fail = None

    def __call__(self, request):
        assert request.url.host == "127.0.0.1"
        assert request.headers["Api-Key"] == "fake-credential"
        path = request.url.path.removeprefix("/admin_api/v1")
        payload = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, payload))

        if (request.method, path) == ("POST", "/campaigns"):
            if self.fail == "create_timeout":
                raise httpx.ReadTimeout("fake-credential", request=request)
            if self.fail == "create_refused":
                return httpx.Response(409, json={"secret": "fake-credential"})
            self.campaign = {"id": 10, **payload}
            return httpx.Response(200, json=self.campaign)
        if (request.method, path) == ("POST", "/streams"):
            position = payload["position"]
            if self.fail == "second_stream" and position == 2:
                return httpx.Response(422, json={"detail": "internal"})
            stream = {"id": 70 + position, **payload}
            self.streams[stream["id"]] = stream
            return httpx.Response(200, json=stream)
        if (request.method, path) == ("POST", "/campaigns/10/enable"):
            if self.fail == "enable_refused":
                return httpx.Response(400, json={"detail": "internal"})
            if self.fail != "enable_ineffective":
                self.campaign["state"] = "active"
            if self.fail == "enable_timeout":
                raise httpx.ReadTimeout("fake-credential", request=request)
            return httpx.Response(200)
        if (request.method, path) == ("GET", "/campaigns/10"):
            return httpx.Response(200, json=self.campaign)
        if (request.method, path) == ("GET", "/campaigns/10/streams"):
            streams = list(self.streams.values())
            if self.fail == "wrong_order":
                streams.reverse()
            return httpx.Response(200, json=streams)
        if (request.method, path) in {("GET", "/streams/71"), ("GET", "/streams/72")}:
            stream = self.streams[int(path.rsplit("/", 1)[-1])].copy()
            if self.fail == "wrong_geo" and stream["id"] == 71:
                stream["filters"] = [{"name": "country", "mode": "reject", "payload": ["IT"]}]
            if self.fail == "wrong_offer" and stream["id"] == 72:
                stream["offers"] = [{"offer_id": 12, "share": 50, "state": "active"}]
            return httpx.Response(200, json=stream)
        if (request.method, path) == ("GET", "/offers/12"):
            return httpx.Response(200, json={"id": 12, "state": "active"})
        if request.method == "GET" and path.startswith("/offers/"):
            return httpx.Response(404, json={"detail": "internal"})
        if (request.method, path) == ("GET", "/domains"):
            return httpx.Response(200, json=[] if self.bad_catalog else [{"id": 31}])
        if (request.method, path) == ("GET", "/groups"):
            assert request.url.params.get("type") == "campaigns"
            return httpx.Response(200, json=[{"id": 32}])
        if (request.method, path) == ("GET", "/traffic_sources"):
            return httpx.Response(200, json=[{"id": 33}])
        raise AssertionError(f"Unexpected method/path: {request.method} {path}")

    def mutations(self):
        return [entry for entry in self.requests if entry[0] != "GET"]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    for name, value in {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'isolated.db'}",
        "KEITARO_BASE_URL": "http://127.0.0.1:9",
        "KEITARO_API_KEY": "fake-credential",
        "KEITARO_TEST_PREFIX": "test_example_",
        "KEITARO_DOMAIN_ID": "31",
        "KEITARO_GROUP_ID": "32",
        "KEITARO_TRAFFIC_SOURCE_ID": "33",
    }.items():
        monkeypatch.setenv(name, value)
    upstream = FakeUpstream()
    adapter = KeitaroClient(load_settings(), transport=httpx.MockTransport(upstream))
    app.dependency_overrides[get_keitaro_client] = lambda: adapter
    try:
        with TestClient(app) as browser:
            yield browser, upstream
    finally:
        app.dependency_overrides.pop(get_keitaro_client, None)
        adapter.close()


def test_create_with_verified_routes_and_reference_ids(setup):
    browser, upstream = setup
    response = browser.post(
        "/api/campaigns", json=CAMPAIGN_REQUEST, headers={"Origin": "http://testserver"}
    )

    assert response.status_code == 201
    assert response.json() == {
        "campaign_id": 10,
        "name": "test_example_roma",
        "stream_ids": [71, 72],
    }
    create, redirect, fallback, enable = upstream.mutations()
    assert create[:2] == ("POST", "/campaigns")
    assert {key: value for key, value in create[2].items() if key != "alias"} == CAMPAIGN_FIELDS
    assert create[2]["alias"].startswith("kw_") and create[2]["alias"].isascii()
    assert redirect == (
        "POST",
        "/streams",
        {
            "campaign_id": 10,
            "name": "Country redirect",
            "type": "forced",
            "position": 1,
            "schema": "redirect",
            "state": "active",
            "action_type": "http",
            "action_payload": "https://google.com",
            "filters": [{"name": "country", "mode": "accept", "payload": ["IT"]}],
        },
    )
    assert fallback == (
        "POST",
        "/streams",
        {
            "campaign_id": 10,
            "name": "Fallback offer",
            "type": "default",
            "position": 2,
            "schema": "landings",
            "state": "active",
            "action_type": "http",
            "filters": [],
            "landings": [],
            "offers": [{"offer_id": 12, "share": 100, "state": "active"}],
        },
    )
    assert enable == ("POST", "/campaigns/10/enable", None)
    assert [(method, path) for method, path, _ in upstream.requests] == [
        ("GET", "/offers/12"),
        ("GET", "/domains"),
        ("GET", "/groups"),
        ("GET", "/traffic_sources"),
        ("POST", "/campaigns"),
        ("GET", "/campaigns/10"),
        ("POST", "/streams"),
        ("POST", "/streams"),
        ("GET", "/campaigns/10/streams"),
        ("GET", "/streams/71"),
        ("GET", "/streams/72"),
        ("POST", "/campaigns/10/enable"),
        ("GET", "/campaigns/10"),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "other_roma"),
        ("name", "test_example_"),
        ("country_code", "ZZ"),
        ("offer_id", 0),
    ],
)
def test_invalid_request_before_contacting_keitaro(setup, field, value):
    browser, upstream = setup
    response = browser.post("/api/campaigns", json={**CAMPAIGN_REQUEST, field: value})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert upstream.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://other.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
    ],
)
def test_cross_origin_cannot_mutate_campaigns(setup, headers):
    browser, upstream = setup
    response = browser.post("/api/campaigns", json=CAMPAIGN_REQUEST, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert upstream.requests == []


def test_missing_offer_has_no_mutations(setup):
    browser, upstream = setup
    response = browser.post("/api/campaigns", json={**CAMPAIGN_REQUEST, "offer_id": 999})

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Оффер № 999 не найден.",
    }
    assert upstream.requests == [("GET", "/offers/999", None)]


def test_missing_configured_reference_has_no_mutations(setup):
    browser, upstream = setup
    upstream.bad_catalog = True
    response = browser.post("/api/campaigns", json=CAMPAIGN_REQUEST)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "KEITARO_ERROR"
    assert "Домен № 31" in response.json()["error"]["message"]
    assert upstream.mutations() == []


@pytest.mark.parametrize(
    "failure", ["second_stream", "wrong_order", "wrong_geo", "wrong_offer"]
)
def test_stream_failure_leaves_campaign_disabled(setup, failure):
    browser, upstream = setup
    upstream.fail = failure
    response = browser.post("/api/campaigns", json=CAMPAIGN_REQUEST)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PARTIAL_CREATE"
    assert response.json()["error"]["campaign_id"] == 10
    assert len(upstream.mutations()) == 3
    assert upstream.campaign["state"] == "disabled"


@pytest.mark.parametrize("failure", ["enable_refused", "enable_ineffective", "enable_timeout"])
def test_activation_requires_confirmed_success(setup, failure):
    browser, upstream = setup
    upstream.fail = failure
    response = browser.post("/api/campaigns", json=CAMPAIGN_REQUEST)

    assert response.status_code == 502
    expected_code = "CREATE_UNCONFIRMED" if failure == "enable_timeout" else "PARTIAL_CREATE"
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["campaign_id"] == 10
    assert len(upstream.mutations()) == 4


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [("create_refused", "KEITARO_ERROR"), ("create_timeout", "CREATE_UNCONFIRMED")],
)
def test_first_write_failure_has_no_retry_or_known_id(setup, failure, expected_code):
    browser, upstream = setup
    upstream.fail = failure
    response = browser.post("/api/campaigns", json=CAMPAIGN_REQUEST)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == expected_code
    assert "campaign_id" not in response.json()["error"]
    assert "fake-credential" not in response.text
    assert len(upstream.mutations()) == 1


def test_creator_serves_ui_without_key_or_demo_values(setup):
    browser, upstream = setup
    response = browser.get("/")

    assert response.status_code == 200
    assert 'id="create-form"' in response.text and 'id="routing"' in response.text
    assert "test_example_" in response.text
    assert all(f"ID {configured_id}" in response.text for configured_id in (31, 32, 33))
    assert "fake-credential" not in response.text and "test_demo_" not in response.text
    assert browser.get("/static/app.css").status_code == 200
    assert browser.get("/static/app.js").status_code == 200
    assert upstream.requests == []
