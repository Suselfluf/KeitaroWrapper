from typing import Annotated, Literal

import pycountry
from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
)

PositiveId = Annotated[int, Field(strict=True, gt=0)]
Revision = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]+$")]

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "OUTSIDE_TEST_PREFIX",
    "NOT_FOUND",
    "STALE_STREAM",
    "KEITARO_ERROR",
    "PARTIAL_CREATE",
    "CREATE_UNCONFIRMED",
    "WRITE_UNCONFIRMED",
    "STORAGE_ERROR",
]


class CreateCampaign(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    country_code: str
    offer_id: PositiveId

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        code = value.strip()
        if len(code) != 2 or not code.isascii() or not code.isalpha():
            raise ValueError("Укажите двухбуквенный код страны.")
        country_code = code.upper()
        if pycountry.countries.get(alpha_2=country_code) is None:
            raise ValueError("Неизвестный код страны.")
        return country_code


class CreatedCampaign(BaseModel):
    campaign_id: PositiveId
    name: str
    stream_ids: tuple[PositiveId, PositiveId]


class SelectionItem(BaseModel):
    id: PositiveId
    name: str


class OfferResult(SelectionItem):
    pass


class SelectionResults(BaseModel):
    items: list[SelectionItem]


class ActiveOffer(OfferResult):
    share: StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    pinned: StrictBool


class ArchivedOffer(BaseModel):
    id: PositiveId
    name: str | None
    share: Literal[0]


class RedirectStreamView(BaseModel):
    id: PositiveId
    name: str
    kind: Literal["redirect"]


class OffersStreamView(BaseModel):
    id: PositiveId
    name: str
    kind: Literal["offers"]
    revision: Revision
    active: list[ActiveOffer]
    archived: list[ArchivedOffer]


StreamView = Annotated[RedirectStreamView | OffersStreamView, Field(discriminator="kind")]


class CampaignView(BaseModel):
    id: PositiveId
    name: str
    streams: list[StreamView]


class DraftChange(BaseModel):
    revision: Revision
    active_offer_ids: Annotated[list[PositiveId], Field(min_length=1)]

    @field_validator("active_offer_ids")
    @classmethod
    def validate_unique_offers(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("ID офферов не должны повторяться.")
        return value


class PinChange(BaseModel):
    revision: Revision
    pinned: StrictBool


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    campaign_id: PositiveId | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail

    def as_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)
