import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.revision_store import Conflict, RevisionStore


def test_history_rollback_and_read_only(tmp_path):
    path = tmp_path / 'nested' / 'store.db'
    store = RevisionStore(path)
    assert store.read() == {'revision': 0, 'data': {}}
    assert not path.exists()
    with pytest.raises(ValueError):
        store.read(1)
    assert store.commit(0, lambda _: {'value': 1})['revision'] == 1
    with pytest.raises(Conflict) as err:
        store.commit(0, lambda _: {})
    assert err.value.actual == 1
    def bad(value):
        value['value'] = 100
        raise RuntimeError('abort')
    with pytest.raises(RuntimeError):
        store.commit(1, bad)
    assert store.read()['data'] == {'value': 1}
    store.commit(1, lambda value: {'value': value['value'] + 1})
    assert store.read(1)['data']['value'] == 1
    assert store.read()['data']['value'] == 2
    with pytest.raises(ValueError):
        store.read(9)
    for invalid in (-1, True, '1'):
        with pytest.raises(ValueError):
            store.commit(invalid, lambda _: {})
    with pytest.raises(ValueError):
        store.commit(2, lambda _: [])


def test_competing_writers(tmp_path):
    path = tmp_path / 'store.db'
    RevisionStore(path).commit(0, lambda _: {})
    def write(_):
        try:
            return RevisionStore(path).commit(1, lambda _: {'winner': True})['revision']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(map(str, results)) == ['2', 'conflict']
