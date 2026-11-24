import hashlib
import json
import math
from threading import Lock

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.contracts import (
    ActiveOffer,
    ArchivedOffer,
    CampaignView,
    DraftChange,
    ErrorCode,
    OfferResult,
    OffersStreamView,
    PinChange,
    RedirectStreamView,
    StreamView,
)
from app.editor_weights import redistribute_shares
from app.keitaro import JsonObject, KeitaroClient, KeitaroError
from app.storage import StreamOfferMemory

_STREAM_LOCKS = tuple(Lock() for _ in range(64))


class EditorError(RuntimeError):
    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _invalid_upstream() -> EditorError:
    return EditorError(502, "KEITARO_ERROR", "Keitaro вернул данные неожиданного формата.")


def _read_error(error: KeitaroError, missing_message: str = "") -> EditorError:
    if error.status_code == 404 and missing_message:
        return EditorError(404, "NOT_FOUND", missing_message)
    return EditorError(502, "KEITARO_ERROR", str(error))


def _stream_id(stream: JsonObject) -> int:
    stream_id = stream.get("id")
    if type(stream_id) is not int or stream_id <= 0:
        raise _invalid_upstream()
    return stream_id


def _stream_name(stream: JsonObject) -> str:
    name = stream.get("name")
    if not isinstance(name, str):
        raise _invalid_upstream()
    return name


def _read_offers(stream: JsonObject) -> tuple[dict[int, int | float], list[JsonObject]]:
    rows = stream.get("offers")
    if not isinstance(rows, list):
        raise _invalid_upstream()
    shares: dict[int, int | float] = {}
    disabled: list[JsonObject] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("state") not in ("active", "disabled"):
            raise _invalid_upstream()
        offer_id, share = row.get("offer_id"), row.get("share")
        if (
            type(offer_id) is not int
            or offer_id <= 0
            or offer_id in seen
            or type(share) not in (int, float)
            or not 0 <= share <= 100
            or not math.isfinite(share)
        ):
            raise _invalid_upstream()
        seen.add(offer_id)
        if row["state"] == "active":
            shares[offer_id] = share
        else:
            disabled.append({"offer_id": offer_id, "share": share, "state": "disabled"})
    return shares, disabled


def _revision(shares: dict[int, int | float], pins: set[int]) -> str:
    data = [(offer_id, share, offer_id in pins) for offer_id, share in shares.items()]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()


def _check_revision(current: str, expected: str) -> None:
    if current != expected:
        raise EditorError(
            409, "STALE_STREAM", "Поток изменился. Загрузите актуальные потоки перед отправкой."
        )


def _load_memory(session: Session, stream_id: int) -> dict[int, StreamOfferMemory]:
    rows = session.scalars(
        select(StreamOfferMemory).where(StreamOfferMemory.stream_id == stream_id)
    )
    return {row.offer_id: row for row in rows}


def _remember(
    session: Session,
    stream_id: int,
    memory: dict[int, StreamOfferMemory],
    shares: dict[int, int | float],
) -> None:
    for offer_id, row in memory.items():
        if offer_id not in shares:
            row.pinned = False
    for offer_id in shares:
        if offer_id not in memory:
            row = StreamOfferMemory(stream_id=stream_id, offer_id=offer_id, pinned=False)
            session.add(row)
            memory[offer_id] = row


class Editor:
    def __init__(self, client: KeitaroClient, engine: Engine, prefix: str) -> None:
        self._client = client
        self._engine = engine
        self._prefix = prefix
        self._offer_info: dict[int, tuple[str | None, bool]] = {}

    def _campaign_name(self, campaign_id: int) -> str:
        try:
            campaign = self._client.get_campaign(campaign_id)
        except KeitaroError as error:
            raise _read_error(error, "Кампания не найдена.") from None
        name = campaign.get("name")
        if campaign.get("id") != campaign_id or not isinstance(name, str):
            raise _invalid_upstream()
        if not name.startswith(self._prefix):
            raise EditorError(
                403,
                "OUTSIDE_TEST_PREFIX",
                "Эта кампания находится вне вашего тестового префикса.",
            )
        return name

    def _streams(self, campaign_id: int) -> list[JsonObject]:
        try:
            return self._client.list_campaign_streams(campaign_id)
        except KeitaroError as error:
            raise _read_error(error, "Кампания не найдена.") from None

    def _stream(self, campaign_id: int, stream_id: int) -> JsonObject:
        try:
            stream = self._client.get_stream(stream_id)
        except KeitaroError as error:
            raise _read_error(error, "Поток не найден.") from None
        if stream.get("campaign_id") != campaign_id or _stream_id(stream) != stream_id:
            raise EditorError(404, "NOT_FOUND", "Поток не найден в этой кампании.")
        if stream.get("schema") in ("redirect", "action"):
            raise EditorError(422, "VALIDATION_ERROR", "Этот поток не поддерживает офферы.")
        if stream.get("schema") != "landings":
            raise _invalid_upstream()
        return stream

    def _editable_stream(self, campaign_id: int, stream_id: int) -> JsonObject:
        self._campaign_name(campaign_id)
        if not any(_stream_id(stream) == stream_id for stream in self._streams(campaign_id)):
            raise EditorError(404, "NOT_FOUND", "Поток не найден в этой кампании.")
        return self._stream(campaign_id, stream_id)

    def _offer_name(self, offer_id: int, *, required: bool = False) -> str | None:
        if offer_id not in self._offer_info:
            try:
                offer = self._client.get_offer(offer_id)
            except KeitaroError as error:
                if error.status_code != 404:
                    raise _read_error(error) from None
                self._offer_info[offer_id] = (None, False)
            else:
                name = offer.get("name")
                if offer.get("id") != offer_id or not isinstance(name, str):
                    raise _invalid_upstream()
                self._offer_info[offer_id] = (name, offer.get("state", "active") == "active")
        name, available = self._offer_info[offer_id]
        if required and name is None:
            raise EditorError(404, "NOT_FOUND", f"Оффер № {offer_id} не найден.")
        if required and not available:
            raise EditorError(404, "NOT_FOUND", f"Оффер № {offer_id} недоступен.")
        return name

    def find_offer(self, offer_id: int) -> OfferResult:
        name = self._offer_name(offer_id, required=True)
        if name is None:
            raise EditorError(404, "NOT_FOUND", f"Оффер № {offer_id} не найден.")
        return OfferResult(id=offer_id, name=name)

    def _view(
        self,
        stream: JsonObject,
        shares: dict[int, int | float],
        pins: set[int],
        known: set[int],
        revision: str,
    ) -> OffersStreamView:
        return OffersStreamView(
            kind="offers",
            id=_stream_id(stream),
            name=_stream_name(stream),
            active=[
                ActiveOffer(
                    id=offer_id,
                    name=self._offer_name(offer_id) or f"Оффер № {offer_id}",
                    share=share,
                    pinned=offer_id in pins,
                )
                for offer_id, share in shares.items()
            ],
            archived=[
                ArchivedOffer(id=offer_id, share=0, name=self._offer_name(offer_id))
                for offer_id in sorted(known - shares.keys())
            ],
            revision=revision,
        )

    def sync(self, campaign_id: int) -> CampaignView:
        name = self._campaign_name(campaign_id)
        views: list[StreamView] = []
        for listed in self._streams(campaign_id):
            stream_id = _stream_id(listed)
            if listed.get("schema") in ("redirect", "action"):
                views.append(
                    RedirectStreamView(id=stream_id, name=_stream_name(listed), kind="redirect")
                )
                continue
            with (
                _STREAM_LOCKS[stream_id % len(_STREAM_LOCKS)],
                Session(self._engine) as session,
            ):
                stream = self._stream(campaign_id, stream_id)
                shares, _ = _read_offers(stream)
                memory = _load_memory(session, stream_id)
                _remember(session, stream_id, memory, shares)
                pins = {offer_id for offer_id, row in memory.items() if row.pinned}
                views.append(
                    self._view(stream, shares, pins, set(memory), _revision(shares, pins))
                )
                session.commit()
        return CampaignView(id=campaign_id, name=name, streams=views)

    def change_offers(
        self, campaign_id: int, stream_id: int, change: DraftChange, *, publish: bool
    ) -> OffersStreamView:
        with _STREAM_LOCKS[stream_id % len(_STREAM_LOCKS)], Session(self._engine) as session:
            stream = self._editable_stream(campaign_id, stream_id)
            current, _ = _read_offers(stream)
            memory = _load_memory(session, stream_id)
            pins = {
                offer_id
                for offer_id, row in memory.items()
                if row.pinned and offer_id in current
            }
            revision = _revision(current, pins)
            _check_revision(revision, change.revision)
            requested = change.active_offer_ids
            for offer_id in requested:
                if offer_id not in current:
                    self._offer_name(offer_id, required=True)
            pinned_shares = {
                offer_id: int(share) if int(share) == share else share
                for offer_id, share in current.items()
                if offer_id in pins
            }
            try:
                weights = redistribute_shares(requested, pinned_shares)
            except ValueError as error:
                raise EditorError(422, "VALIDATION_ERROR", str(error)) from None
            proposed = dict(zip(requested, weights, strict=True))
            known = set(memory) | current.keys()
            view = self._view(stream, proposed, pins & current.keys(), known, revision)
            if not publish:
                return view

            latest, disabled = _read_offers(self._editable_stream(campaign_id, stream_id))
            _check_revision(_revision(latest, pins), revision)
            confirmed = self._publish(campaign_id, stream_id, proposed, disabled)
            try:
                _remember(session, stream_id, memory, current)
                _remember(session, stream_id, memory, confirmed)
                session.commit()
            except SQLAlchemyError:
                raise EditorError(
                    503,
                    "STORAGE_ERROR",
                    "Изменения подтверждены в Keitaro, но история не сохранилась. Загрузите потоки.",
                ) from None
            return view.model_copy(
                update={"revision": _revision(confirmed, pins & current.keys())}
            )

    def _publish(
        self,
        campaign_id: int,
        stream_id: int,
        proposed: dict[int, int],
        disabled: list[JsonObject],
    ) -> dict[int, int | float]:
        rows: list[JsonObject] = [
            {"offer_id": offer_id, "share": share, "state": "active"}
            for offer_id, share in proposed.items()
        ]
        preserved = [row for row in disabled if row["offer_id"] not in proposed]
        rows.extend(preserved)
        message = (
            "Запись в Keitaro не подтверждена. Загрузите потоки перед повторной отправкой."
        )
        try:
            self._client.set_stream_offers(stream_id, rows)
        except KeitaroError as error:
            if error.status_code is not None and 400 <= error.status_code < 500:
                raise EditorError(502, "KEITARO_ERROR", "Keitaro отклонил изменения.") from None
            raise EditorError(502, "WRITE_UNCONFIRMED", message) from None
        try:
            confirmed, confirmed_disabled = _read_offers(self._stream(campaign_id, stream_id))
        except EditorError:
            raise EditorError(502, "WRITE_UNCONFIRMED", message) from None
        if list(confirmed.items()) != list(proposed.items()) or confirmed_disabled != preserved:
            raise EditorError(502, "WRITE_UNCONFIRMED", message)
        return confirmed

    def pin(
        self, campaign_id: int, stream_id: int, offer_id: int, change: PinChange
    ) -> OffersStreamView:
        with _STREAM_LOCKS[stream_id % len(_STREAM_LOCKS)], Session(self._engine) as session:
            stream = self._editable_stream(campaign_id, stream_id)
            current, _ = _read_offers(stream)
            memory = _load_memory(session, stream_id)
            pins = {known_id for known_id, row in memory.items() if row.pinned}
            _check_revision(_revision(current, pins), change.revision)
            if offer_id not in current:
                raise EditorError(404, "NOT_FOUND", "Оффер не активен в выбранном потоке.")
            row = memory.get(offer_id)
            if row is None:
                row = StreamOfferMemory(stream_id=stream_id, offer_id=offer_id)
                session.add(row)
            row.pinned = change.pinned
            pins = (pins - {offer_id}) | ({offer_id} if change.pinned else set())
            view = self._view(stream, current, pins, set(memory), _revision(current, pins))
            session.commit()
            return view
