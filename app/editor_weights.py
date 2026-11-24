from collections.abc import Mapping, Sequence

_TOTAL_SHARE = 100


def redistribute_shares(
    active_offer_ids: Sequence[int], pinned_shares: Mapping[int, int]
) -> list[int]:
    """Calculate draft shares in the same order as the active offer IDs."""
    if not active_offer_ids:
        raise ValueError("В потоке должен остаться хотя бы один активный оффер.")
    if len(set(active_offer_ids)) != len(active_offer_ids):
        raise ValueError("ID офферов не должны повторяться.")

    active_pins = {
        offer_id: pinned_shares[offer_id]
        for offer_id in active_offer_ids
        if offer_id in pinned_shares
    }
    if any(
        type(share) is not int or not 0 <= share <= _TOTAL_SHARE
        for share in active_pins.values()
    ):
        raise ValueError("Вес закреплённого оффера должен быть целым числом от 0 до 100%.")

    remaining_share = _TOTAL_SHARE - sum(active_pins.values())
    free_count = len(active_offer_ids) - len(active_pins)
    if free_count == 0:
        if remaining_share != 0:
            raise ValueError("Снимите закрепление, чтобы перераспределить доли до 100%.")
        return [active_pins[offer_id] for offer_id in active_offer_ids]

    if remaining_share < free_count:
        raise ValueError("Освободите вес, чтобы выделить хотя бы 1% каждому свободному офферу.")

    base_share, extra_points = divmod(remaining_share, free_count)
    shares: list[int] = []
    for offer_id in active_offer_ids:
        if offer_id in active_pins:
            shares.append(active_pins[offer_id])
        elif extra_points:
            shares.append(base_share + 1)
            extra_points -= 1
        else:
            shares.append(base_share)
    return shares
