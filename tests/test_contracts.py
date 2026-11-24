import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, load_settings
from app.contracts import CampaignView, CreateCampaign, CreatedCampaign, DraftChange, PinChange

REVISION = "0a" * 32


def test_normalizes_country_and_campaign_name():
    request = CreateCampaign(name=" test_demo_run ", country_code="de", offer_id=24)
    assert request.model_dump() == dict(name="test_demo_run", country_code="DE", offer_id=24)


@pytest.mark.parametrize("geo", ["ZZ", "ß", "D3", "DEU"])
def test_rejects_unknown_or_malformed_country(geo):
    with pytest.raises(ValidationError):
        CreateCampaign(name="test_demo_run", country_code=geo, offer_id=24)


@pytest.mark.parametrize("offer_id", [0, -1, True, "24", 24.5])
def test_requires_positive_integer_id(offer_id):
    with pytest.raises(ValidationError):
        CreateCampaign(name="test_demo_run", country_code="DE", offer_id=offer_id)


@pytest.mark.parametrize("stream_ids", [[1], [1, 2, 3]])
def test_requires_two_created_streams(stream_ids):
    with pytest.raises(ValidationError):
        CreatedCampaign(campaign_id=24, name="test_demo_run", stream_ids=stream_ids)


def test_serializes_campaign_stream_union_and_keeps_read_shares():
    values = {
        "id": 24,
        "name": "test_demo_run",
        "streams": [
            {"id": 1, "name": "Google", "kind": "redirect"},
            {
                "id": 2,
                "name": "Офферы",
                "kind": "offers",
                "revision": REVISION,
                "active": [{"id": 24, "name": "Оффер", "share": 25.5, "pinned": True}],
                "archived": [{"id": 25, "name": None, "share": 0}],
            },
        ],
    }

    assert CampaignView.model_validate(values).model_dump(mode="json") == values


@pytest.mark.parametrize("offer_ids", [[], [24, 24], [0], ["24"]])
def test_rejects_invalid_draft_composition(offer_ids):
    with pytest.raises(ValidationError):
        DraftChange(revision=REVISION, active_offer_ids=offer_ids)


@pytest.mark.parametrize("revision", ["f" * 63, "g" * 64, "f" * 65])
def test_rejects_invalid_revision(revision):
    with pytest.raises(ValidationError):
        PinChange(revision=revision, pinned=True)


def test_rejects_string_pin():
    with pytest.raises(ValidationError):
        PinChange(revision=REVISION, pinned="false")


@pytest.fixture
def configured_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("KEITARO_BASE_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("KEITARO_API_KEY", "dummy-key-do-not-use")
    monkeypatch.setenv("KEITARO_TEST_PREFIX", "test_demo_")
    monkeypatch.setenv("KEITARO_DOMAIN_ID", "1")
    monkeypatch.setenv("KEITARO_GROUP_ID", "2")
    monkeypatch.setenv("KEITARO_TRAFFIC_SOURCE_ID", "3")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")


def test_loads_config_and_wraps_api_key(configured_environment):
    settings = load_settings()
    assert settings.keitaro_base_url == "http://127.0.0.1:9"
    assert settings.keitaro_domain_id == 1
    assert settings.keitaro_api_key.get_secret_value() == "dummy-key-do-not-use"
    assert "dummy-key-do-not-use" not in repr(settings)


def test_rejects_missing_key(configured_environment, monkeypatch):
    monkeypatch.setenv("KEITARO_API_KEY", "")
    with pytest.raises(ConfigurationError, match="KEITARO_API_KEY"):
        load_settings()


@pytest.mark.parametrize(
    ("variable", "invalid"),
    [
        ("KEITARO_BASE_URL", "https://user:confidential@example.org"),
        ("KEITARO_GROUP_ID", "0"),
        ("KEITARO_TEST_PREFIX", "test__"),
        ("DATABASE_URL", "postgresql://user:confidential@example.org/db"),
    ],
)
def test_rejects_invalid_settings_without_exposing_values(
    configured_environment, monkeypatch, variable, invalid
):
    monkeypatch.setenv(variable, invalid)
    with pytest.raises(ConfigurationError, match=variable) as exc:
        load_settings()
    assert invalid not in str(exc.value)
    assert "confidential" not in str(exc.value)
