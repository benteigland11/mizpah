import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.revision_store import RevisionStore

with TemporaryDirectory() as directory:
    store = RevisionStore(Path(directory) / 'state.db')
    saved = store.commit(0, lambda _: {'message': 'retained'})
    assert store.read() == saved
    print(saved)
