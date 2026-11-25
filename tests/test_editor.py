import json
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_settings
from app.keitaro import KeitaroClient
from app.main import app, get_keitaro_client
from app.storage import Base, StreamOfferMemory

SYNC = "/api/campaigns/10/sync"
STREAM = "/api/campaigns/10/streams/72"
OFFERS = {
    74: {"id": 74, "name": "Атлас", "state": "active"},
    75: {"id": 75, "name": "Маяк", "state": "active"},
    76: {"id": 76, "name": "Старт", "state": "active"},
}


def rows(*items, state="active"):
    return [{"offer_id": offer_id, "share": share, "state": state} for offer_id, share in items]


class FakeKeitaro:
    def __init__(self):
        self.campaign = {"id": 10, "name": "test_example_roma"}
        self.offers = deepcopy(OFFERS)
        self.stream = {
            "id": 72,
            "name": "Офферы",
            "schema": "landings",
            "campaign_id": 10,
            "offers": rows((74, 25), (75, 75)),
        }
        self.redirect = {"id": 71, "name": "Страна", "schema": "redirect", "campaign_id": 10}
        self.requests = []
        self.writes = []
        self.failure = None
        self.after_lookup = None

    def __call__(self, request):
        assert request.url.host == "127.0.0.1"
        path = request.url.path.removeprefix("/admin_api/v1")
        method = request.method
        self.requests.append((method, path))
        if method == "PUT":
            self.writes.append(json.loads(request.content))
            if self.failure == "refused":
                return httpx.Response(400, json={"detail": "internal"})
            if self.failure == "transport":
                raise httpx.ReadTimeout("internal", request=request)
            if self.failure != "ineffective":
                self.stream["offers"] = self.writes[-1]["offers"]
            return httpx.Response(200)
        if path == "/campaigns/10":
            return httpx.Response(200, json=self.campaign)
        if path == "/campaigns/10/streams":
            return httpx.Response(200, json=[self.redirect, self.stream])
        if path == "/streams/72":
            return httpx.Response(200, json=self.stream)
        if path == "/streams/71":
            return httpx.Response(200, json=self.redirect)
        if path.startswith("/offers/"):
            offer_id = int(path.rsplit("/", 1)[-1])
            if offer_id == self.after_lookup:
                self.stream["offers"] = rows((74, 30), (75, 70))
            offer = self.offers.get(offer_id)
            return httpx.Response(200 if offer else 404, json=offer)
        raise AssertionError((method, path))


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
    remote = FakeKeitaro()
    upstream = KeitaroClient(load_settings(), transport=httpx.MockTransport(remote))
    app.dependency_overrides[get_keitaro_client] = lambda: upstream
    try:
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            yield client, remote
    finally:
        app.dependency_overrides.pop(get_keitaro_client)
        upstream.close()


def sync(client):
    response = client.post(SYNC)
    assert response.status_code == 200, response.text
    return response.json()["streams"]


def memory():
    with Session(app.state.engine) as session:
        query = select(StreamOfferMemory).order_by(StreamOfferMemory.offer_id)
        return [(row.offer_id, row.pinned) for row in session.scalars(query)]


def weights(view):
    return [(row["id"], row["share"]) for row in view["active"]]


def body(view, ids):
    return {"revision": view["revision"], "active_offer_ids": ids}


def test_sync_shows_both_streams_without_renormalizing_and_remembers_active(setup):
    client, remote = setup
    remote.stream["offers"] = rows((74, 25.5), (75, 25))
    redirect, stream = sync(client)
    assert redirect == {"id": 71, "name": "Страна", "kind": "redirect"}
    assert weights(stream) == [(74, 25.5), (75, 25)]
    assert len(stream["revision"]) == 64
    assert memory() == [(74, False), (75, False)]
    assert not remote.writes


def test_search_and_draft_are_read_only(setup):
    client, remote = setup
    stream = sync(client)[1]
    assert client.get("/api/offers/76").json() == {"id": 76, "name": "Старт"}
    draft = client.post(STREAM + "/draft", json=body(stream, [74, 75, 76])).json()
    assert weights(draft) == [(74, 34), (75, 33), (76, 33)]
    assert draft["revision"] == stream["revision"]
    assert memory() == [(74, False), (75, False)]
    assert not remote.writes


def test_refuses_fractional_pin_without_changing_current_weight(setup):
    client, remote = setup
    remote.stream["offers"] = rows((74, 25.5), (75, 75))
    stream = sync(client)[1]

    response = client.put(
        STREAM + "/offers/74/pin", json={"revision": stream["revision"], "pinned": True}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "VALIDATION_ERROR", "message": "Закрепить можно только целый вес."}
    }
    assert memory() == [(74, False), (75, False)]
    assert sync(client)[1] == stream
    assert weights(stream) == [(74, 25.5), (75, 75)]
    assert not remote.writes


def test_unpins_offer_whose_weight_became_fractional(setup):
    client, remote = setup
    stream = sync(client)[1]
    response = client.put(
        STREAM + "/offers/74/pin", json={"revision": stream["revision"], "pinned": True}
    )
    assert response.status_code == 200
    remote.stream["offers"] = rows((74, 25.5), (75, 75))
    fractional = sync(client)[1]
    assert fractional["active"][0]["pinned"] is True

    response = client.put(
        STREAM + "/offers/74/pin", json={"revision": fractional["revision"], "pinned": False}
    )

    assert response.status_code == 200
    unpinned = response.json()
    assert unpinned["active"][0]["pinned"] is False
    assert weights(unpinned) == [(74, 25.5), (75, 75)]
    assert memory() == [(74, False), (75, False)]
    assert not remote.writes


@pytest.mark.parametrize("initial_share", [25, 25.0])
def test_pin_then_publish_and_restore(setup, initial_share):
    client, remote = setup
    remote.stream["offers"] = rows((74, initial_share), (75, 75))
    stream = sync(client)[1]
    response = client.put(
        STREAM + "/offers/74/pin", json={"revision": stream["revision"], "pinned": True}
    )
    assert response.status_code == 200
    pinned = response.json()
    assert weights(pinned) == weights(stream)
    assert pinned["active"][0]["pinned"]
    assert pinned["revision"] != stream["revision"]
    assert not remote.writes
    assert memory() == [(74, True), (75, False)]

    saved = client.put(STREAM + "/offers", json=body(pinned, [74, 75, 76])).json()
    assert weights(saved) == [(74, 25), (75, 38), (76, 37)]
    assert remote.writes == [{"offers": rows((74, 25), (75, 38), (76, 37))}]
    assert saved["revision"] == sync(client)[1]["revision"]

    removed = client.put(STREAM + "/offers", json=body(saved, [74, 75])).json()
    assert weights(removed) == [(74, 25), (75, 75)]
    assert removed["archived"] == [{"id": 76, "name": "Старт", "share": 0}]
    restored = client.put(STREAM + "/offers", json=body(removed, [74, 75, 76])).json()
    assert weights(restored) == weights(saved)
    assert restored["archived"] == []


def test_external_removal_archives_and_discards_stale_pin(setup):
    client, remote = setup
    remote.stream["offers"] = rows((74, 25), (75, 25), (76, 50))
    stream = sync(client)[1]
    response = client.put(
        STREAM + "/offers/76/pin", json={"revision": stream["revision"], "pinned": True}
    )
    assert response.status_code == 200
    remote.stream["offers"] = rows((74, 25), (75, 25))
    del remote.offers[76]
    fresh = sync(client)[1]
    assert weights(fresh) == [(74, 25), (75, 25)]
    assert fresh["archived"] == [{"id": 76, "name": None, "share": 0}]
    assert memory() == [(74, False), (75, False), (76, False)]


@pytest.mark.parametrize(
    "failure,code",
    [
        ("refused", "KEITARO_ERROR"),
        ("transport", "WRITE_UNCONFIRMED"),
        ("ineffective", "WRITE_UNCONFIRMED"),
    ],
)
def test_unconfirmed_remote_write_never_saves_history(setup, failure, code):
    client, remote = setup
    stream = sync(client)[1]
    remote.failure = failure
    response = client.put(STREAM + "/offers", json=body(stream, [74, 75, 76]))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == code
    assert "internal" not in response.text
    assert len(remote.writes) == 1
    assert memory() == [(74, False), (75, False)]


def test_pin_and_publish_reject_external_reordering(setup):
    client, remote = setup
    stream = sync(client)[1]
    remote.stream["offers"].reverse()
    pin = client.put(
        STREAM + "/offers/74/pin", json={"revision": stream["revision"], "pinned": True}
    )
    assert (pin.status_code, pin.json()["error"]["code"]) == (409, "STALE_STREAM")
    saved = client.put(STREAM + "/offers", json=body(stream, [74, 75, 76]))
    assert (saved.status_code, saved.json()["error"]["code"]) == (409, "STALE_STREAM")
    assert not remote.writes
    assert memory() == [(74, False), (75, False)]


def test_publish_rechecks_after_offer_lookup(setup):
    client, remote = setup
    stream = sync(client)[1]
    remote.after_lookup = 76
    response = client.put(STREAM + "/offers", json=body(stream, [74, 75, 76]))
    assert (response.status_code, response.json()["error"]["code"]) == (409, "STALE_STREAM")
    assert not remote.writes


def test_wrong_prefix_refuses_without_a_write(setup):
    client, remote = setup
    stream = sync(client)[1]
    remote.campaign["name"] = "test_someone_rome"
    response = client.put(STREAM + "/offers", json=body(stream, [74, 75, 76]))
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "OUTSIDE_TEST_PREFIX",
    )
    assert not remote.writes


def test_invalid_membership_refuses_without_a_write(setup):
    client, remote = setup
    stream = sync(client)[1]
    remote.stream["campaign_id"] = 11
    response = client.put(STREAM + "/offers", json=body(stream, [74, 76]))
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert not remote.writes


def test_cross_site_refuses_before_any_keitaro_request(setup):
    client, remote = setup
    response = client.put(
        STREAM + "/offers",
        json={"revision": "0" * 64, "active_offer_ids": [74]},
        headers={"Origin": "https://elsewhere.example"},
    )
    assert response.status_code == 403
    assert not remote.requests


@pytest.mark.parametrize("ids", [[], [74, 74]])
def test_invalid_draft_shape_is_rejected(setup, ids):
    client, remote = setup
    stream = sync(client)[1]
    response = client.post(STREAM + "/draft", json=body(stream, ids))
    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    assert not remote.writes


def test_pinned_100_percent_refuses_overallocation(setup):
    client, remote = setup
    remote.stream["offers"] = rows((74, 100))
    stream = sync(client)[1]
    pinned = client.put(
        STREAM + "/offers/74/pin", json={"revision": stream["revision"], "pinned": True}
    ).json()
    response = client.post(STREAM + "/draft", json=body(pinned, [74, 75]))
    assert response.status_code == 422
    assert "Освободите вес" in response.json()["error"]["message"]
    assert not remote.writes


@pytest.mark.parametrize("availability", [None, "deleted"])
def test_deleted_or_missing_catalogue_offer_cannot_be_added(setup, availability):
    client, remote = setup
    stream = sync(client)[1]
    if availability is None:
        del remote.offers[76]
    else:
        remote.offers[76]["state"] = availability
    assert client.get("/api/offers/76").status_code == 404
    assert client.post(STREAM + "/draft", json=body(stream, [74, 75, 76])).status_code == 404
    assert not remote.writes


def test_inactive_row_is_preserved_until_explicitly_activated(setup):
    client, remote = setup
    inactive = rows((76, 30), state="disabled")
    remote.stream["offers"].extend(inactive)
    stream = sync(client)[1]
    assert weights(stream) == [(74, 25), (75, 75)]
    assert stream["archived"] == []
    reduced = client.put(STREAM + "/offers", json=body(stream, [74])).json()
    assert remote.writes[-1] == {"offers": rows((74, 100)) + inactive}
    activated = client.put(STREAM + "/offers", json=body(reduced, [74, 76])).json()
    assert weights(activated) == [(74, 50), (76, 50)]
    assert remote.writes[-1] == {"offers": rows((74, 50), (76, 50))}


def test_unknown_wire_state_refuses_without_a_write(setup):
    client, remote = setup
    stream = sync(client)[1]
    remote.stream["offers"][0]["state"] = "unknown"
    response = client.put(STREAM + "/offers", json=body(stream, [74]))
    assert (response.status_code, response.json()["error"]["code"]) == (502, "KEITARO_ERROR")
    assert not remote.writes
