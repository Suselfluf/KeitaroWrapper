from collections.abc import Iterable, Iterator

from app.contracts import SelectionItem, SelectionResults
from app.keitaro import JsonObject, KeitaroClient, KeitaroError

_RESULT_LIMIT = 20
_CAMPAIGN_PAGE_SIZE = 100


def search_offers(client: KeitaroClient, query: str) -> SelectionResults:
    active_offers = (row for row in client.list_offers() if row.get("state") == "active")
    return _matching_items(active_offers, query)


def search_campaigns(client: KeitaroClient, prefix: str, query: str) -> SelectionResults:
    own_campaigns = (
        row
        for row in _campaign_pages(client)
        if isinstance(name := row.get("name"), str) and name.startswith(prefix)
    )
    return _matching_items(own_campaigns, query)


def _campaign_pages(client: KeitaroClient) -> Iterator[JsonObject]:
    offset = 0
    seen_ids: set[int] = set()
    while page := client.list_campaigns(offset=offset, limit=_CAMPAIGN_PAGE_SIZE):
        new_rows = []
        for row in page:
            item_id = _positive_id(row)
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                new_rows.append(row)
        if not new_rows:
            raise KeitaroError("invalid_response")
        yield from new_rows
        offset += len(page)


def _positive_id(row: JsonObject) -> int:
    item_id = row.get("id")
    if type(item_id) is not int or item_id <= 0:
        raise KeitaroError("invalid_response")
    return item_id


def _matching_items(rows: Iterable[JsonObject], query: str) -> SelectionResults:
    needle = query.strip().casefold()
    matches = []
    for row in rows:
        item_id = _positive_id(row)
        name = row.get("name")
        if not isinstance(name, str):
            raise KeitaroError("invalid_response")
        if needle in str(item_id) or needle in name.casefold():
            matches.append(SelectionItem(id=item_id, name=name))
    matches.sort(key=lambda item: (str(item.id) != needle, item.id))
    return SelectionResults(items=matches[:_RESULT_LIMIT])
