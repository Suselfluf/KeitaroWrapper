import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.keitaro import KeitaroClient
from app.main import app, get_keitaro_client


class CatalogueUpstream:
    def __init__(self):
        self.offers = []
        self.campaigns = []
        self.offsets = []
        self.page_cap = 100
        self.repeat_page = False
        self.fail = False

    def __call__(self, request):
        assert request.url.host == "127.0.0.1"
        assert request.method == "GET"
        assert request.headers["Api-Key"] == "selection-fixture-key"
        path = request.url.path
        if self.fail:
            return httpx.Response(401, json={"details": "private upstream error"})
        if path == "/admin_api/v1/offers":
            return httpx.Response(200, json=self.offers)
        if path == "/admin_api/v1/campaigns":
            limit = min(int(request.url.params["limit"]), self.page_cap)
            offset = int(request.url.params["offset"])
            self.offsets.append(offset)
            if self.repeat_page:
                offset = 0
            return httpx.Response(200, json=self.campaigns[offset : offset + limit])
        raise AssertionError(f"Unexpected request: {path}")


@pytest.fixture
def selection_setup(monkeypatch, tmp_path):
    for key, value in {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'selection.db'}",
        "KEITARO_BASE_URL": "http://127.0.0.1:9",
        "KEITARO_API_KEY": "selection-fixture-key",
        "KEITARO_TEST_PREFIX": "test_mine_",
        "KEITARO_DOMAIN_ID": "31",
        "KEITARO_GROUP_ID": "32",
        "KEITARO_TRAFFIC_SOURCE_ID": "33",
    }.items():
        monkeypatch.setenv(key, value)
    upstream = CatalogueUpstream()
    client = KeitaroClient(load_settings(), transport=httpx.MockTransport(upstream))
    monkeypatch.setitem(app.dependency_overrides, get_keitaro_client, lambda: client)
    try:
        with TestClient(app) as browser:
            yield browser, upstream
    finally:
        client.close()


def test_offers_filter_query_and_put_exact_id_first(selection_setup):
    browser, upstream = selection_setup
    upstream.offers = [
        {"id": 1204, "name": "Atlas", "state": "active"},
        {"id": 204, "name": "Атлас", "state": "active"},
        {"id": 4, "name": "Промо 204", "state": "active"},
        {"id": 6, "name": "Straße", "state": "active"},
    ]

    search = browser.get("/api/offers", params={"q": " 204 "})
    assert search.status_code == 200
    assert [item["id"] for item in search.json()["items"]] == [204, 4, 1204]
    assert browser.get("/api/offers", params={"q": "  аТЛаС "}).json() == {
        "items": [{"id": 204, "name": "Атлас"}]
    }
    assert browser.get("/api/offers", params={"q": "STRASSE"}).json() == {
        "items": [{"id": 6, "name": "Straße"}]
    }
    assert browser.get("/api/offers", params={"q": "no match"}).json() == {"items": []}


def test_offer_suggestions_exclude_inactive_and_missing_states(selection_setup):
    browser, upstream = selection_setup
    upstream.offers = [
        {"id": 204, "name": "Atlas", "state": "active"},
        {"id": 205, "name": "Atlas", "state": "deleted"},
        {"id": 206, "name": "Atlas", "state": "disabled"},
        {"id": 207, "name": "Atlas"},
    ]
    response = browser.get("/api/offers", params={"q": "atlas"})
    assert response.json() == {"items": [{"id": 204, "name": "Atlas"}]}


def test_campaign_prefix_filters_before_limit_and_searches_later_pages(selection_setup):
    browser, upstream = selection_setup
    upstream.campaigns = [
        {"id": item_id, "name": f"test_foreign_atlas_{item_id}"} for item_id in range(1, 101)
    ] + [
        {"id": item_id, "name": f"test_mine_atlas_{item_id}"}
        for item_id in reversed(range(101, 126))
    ]

    search = browser.get("/api/campaigns", params={"q": " ATLAS "})
    assert search.status_code == 200
    assert search.json() == {
        "items": [
            {"id": item_id, "name": f"test_mine_atlas_{item_id}"} for item_id in range(101, 121)
        ]
    }
    assert upstream.offsets == [0, 100, 125]


def test_campaign_search_continues_when_upstream_caps_page_size(selection_setup):
    browser, upstream = selection_setup
    upstream.page_cap = 2
    upstream.campaigns = [
        {"id": 1, "name": "test_mine_first"},
        {"id": 2, "name": "test_mine_second"},
        {"id": 3, "name": "test_mine_third"},
    ]
    assert browser.get("/api/campaigns", params={"q": "third"}).json() == {
        "items": [{"id": 3, "name": "test_mine_third"}]
    }
    assert upstream.offsets == [0, 2, 3]


def test_repeated_campaign_page_is_rejected(selection_setup):
    browser, upstream = selection_setup
    upstream.repeat_page = True
    upstream.campaigns = [{"id": 1, "name": "test_mine_duplicate"}]
    response = browser.get("/api/campaigns")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "KEITARO_ERROR"
    assert upstream.offsets == [0, 1]


def test_offer_default_results_are_limited_in_id_order(selection_setup):
    browser, upstream = selection_setup
    upstream.offers = [
        {"id": item_id, "name": str(item_id), "state": "active"}
        for item_id in reversed(range(1, 26))
    ]
    assert [item["id"] for item in browser.get("/api/offers").json()["items"]] == list(
        range(1, 21)
    )


@pytest.mark.parametrize("resource", ["offers", "campaigns"])
def test_only_id_and_name_are_returned(selection_setup, resource):
    browser, upstream = selection_setup
    rows = getattr(upstream, resource)
    rows.append(
        {
            "id": 123,
            "name": "test_mine_atlas",
            "state": "active",
            "alias": "private alias",
            "payout": "private payout",
            "url": "private URL",
        }
    )
    response = browser.get(f"/api/{resource}")
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": 123, "name": "test_mine_atlas"}]}


@pytest.mark.parametrize("resource", ["offers", "campaigns"])
def test_lookup_error_does_not_disclose_upstream_detail(selection_setup, resource):
    browser, upstream = selection_setup
    upstream.fail = True
    response = browser.get(f"/api/{resource}")
    assert response.status_code == 502
    assert response.json() == {
        "error": {"code": "KEITARO_ERROR", "message": "Поиск в Keitaro недоступен."}
    }
