import pytest

from src import paginate

DEFAULT_MAX_SIZE = 500


def test_basic_first_page():
    r = paginate(list(range(50)), page=1, size=10)
    assert r["items"] == list(range(10))
    assert r["page"] == 1
    assert r["size"] == 10
    assert r["total"] == 50
    assert r["total_pages"] == 5
    assert r["has_next"] is True
    assert r["has_prev"] is False


def test_middle_page():
    r = paginate(list(range(50)), page=3, size=10)
    assert r["items"] == list(range(20, 30))
    assert r["has_next"] is True
    assert r["has_prev"] is True


def test_last_page_exact():
    r = paginate(list(range(50)), page=5, size=10)
    assert r["items"] == list(range(40, 50))
    assert r["has_next"] is False
    assert r["has_prev"] is True


def test_last_page_partial():
    r = paginate(list(range(47)), page=5, size=10)
    assert r["items"] == list(range(40, 47))
    assert r["total"] == 47
    assert r["total_pages"] == 5
    assert r["has_next"] is False


def test_page_past_end_clamps():
    r = paginate(list(range(10)), page=99, size=5)
    assert r["page"] == 2
    assert r["items"] == list(range(5, 10))
    assert r["has_next"] is False


def test_page_below_one_clamps():
    r = paginate(list(range(10)), page=0, size=5)
    assert r["page"] == 1
    assert r["items"] == list(range(0, 5))


def test_negative_page_clamps():
    r = paginate(list(range(10)), page=-3, size=5)
    assert r["page"] == 1


def test_empty_list():
    r = paginate([], page=1, size=10)
    assert r["items"] == []
    assert r["total"] == 0
    assert r["total_pages"] == 1
    assert r["page"] == 1
    assert r["has_next"] is False
    assert r["has_prev"] is False


def test_size_clamped_to_default_max():
    r = paginate(list(range(2000)), page=1, size=10_000)
    assert r["size"] == DEFAULT_MAX_SIZE
    assert len(r["items"]) == DEFAULT_MAX_SIZE


def test_size_clamped_to_custom_max():
    r = paginate(list(range(100)), page=1, size=999, max_size=25)
    assert r["size"] == 25
    assert len(r["items"]) == 25


def test_custom_max_size_one():
    r = paginate(list(range(5)), page=3, size=10, max_size=1)
    assert r["size"] == 1
    assert r["items"] == [2]
    assert r["total_pages"] == 5


def test_invalid_max_size_raises():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=1, size=10, max_size=0)
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=1, size=10, max_size=-5)
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=1, size=10, max_size="500")


def test_size_clamped_to_one():
    r = paginate(list(range(5)), page=1, size=0)
    assert r["size"] == 1
    assert r["items"] == [0]
    assert r["total_pages"] == 5


def test_negative_size_clamps_to_one():
    r = paginate(list(range(5)), page=1, size=-10)
    assert r["size"] == 1


def test_rejects_non_list():
    with pytest.raises(TypeError):
        paginate("not a list", page=1, size=10)
    with pytest.raises(TypeError):
        paginate((1, 2, 3), page=1, size=10)


def test_rejects_non_int_page_or_size():
    with pytest.raises(TypeError):
        paginate([1, 2, 3], page="1", size=10)
    with pytest.raises(TypeError):
        paginate([1, 2, 3], page=1, size="10")


def test_items_are_not_copied():
    sentinel = object()
    r = paginate([sentinel], page=1, size=10)
    assert r["items"][0] is sentinel


def test_dict_items_passthrough():
    items = [{"id": i, "name": f"item-{i}"} for i in range(25)]
    r = paginate(items, page=2, size=10)
    assert r["items"] == items[10:20]


def test_default_args():
    r = paginate(list(range(100)))
    assert r["page"] == 1
    assert r["size"] == 20
    assert len(r["items"]) == 20


def test_size_exactly_default_max_allowed():
    r = paginate(list(range(DEFAULT_MAX_SIZE)), page=1, size=DEFAULT_MAX_SIZE)
    assert r["size"] == DEFAULT_MAX_SIZE
    assert len(r["items"]) == DEFAULT_MAX_SIZE
    assert r["has_next"] is False


def test_total_pages_for_single_item():
    r = paginate([42], page=1, size=10)
    assert r["total_pages"] == 1
    assert r["has_next"] is False
    assert r["has_prev"] is False
