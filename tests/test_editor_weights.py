import pytest

from app.editor_weights import redistribute_shares


def test_keeps_pin_and_divides_remaining_share():
    assert redistribute_shares([92, 71, 15], {92: 25}) == [25, 38, 37]


def test_even_split_awards_remainder_in_offer_order():
    assert redistribute_shares([92, 71, 15], {}) == [34, 33, 33]


def test_pinned_offer_between_free_offers_keeps_its_share():
    assert redistribute_shares([71, 92, 15], {92: 25}) == [38, 25, 37]


def test_ignores_pin_of_removed_offer():
    assert redistribute_shares([71, 15], {92: 100}) == [50, 50]


def test_all_pinned_shares_are_kept_when_they_sum_to_100():
    assert redistribute_shares([71, 92], {92: 25, 71: 75}) == [75, 25]


def test_one_point_per_free_offer_is_enough():
    assert redistribute_shares([92, 71, 15], {92: 98}) == [98, 1, 1]


def test_rejects_removing_last_active_offer():
    with pytest.raises(ValueError, match="хотя бы один активный оффер"):
        redistribute_shares([], {92: 100})


def test_rejects_duplicate_offer_ids():
    with pytest.raises(ValueError, match="не должны повторяться"):
        redistribute_shares([92, 92], {})


@pytest.mark.parametrize("pinned_share", [99, 100])
def test_rejects_when_free_share_is_too_small(pinned_share):
    with pytest.raises(ValueError, match="хотя бы 1%"):
        redistribute_shares([92, 71, 15], {92: pinned_share})


def test_rejects_all_pinned_shares_when_they_do_not_sum_to_100():
    with pytest.raises(ValueError, match="Снимите закрепление"):
        redistribute_shares([92, 71], {92: 25, 71: 25})


@pytest.mark.parametrize("pinned_share", [-1, 101, 25.5, True])
def test_rejects_an_invalid_pinned_share(pinned_share):
    with pytest.raises(ValueError, match="целым числом от 0 до 100%"):
        redistribute_shares([92, 71], {92: pinned_share})
