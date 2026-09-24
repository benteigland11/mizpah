import pytest


@pytest.fixture(autouse=True)
def _own_user_config(tmp_path_factory, monkeypatch):
    """A test never reads or writes the machine's ~/.config/mizpah: each gets an empty user layer of its own."""
    monkeypatch.setenv('MIZPAH_CONFIG_HOME', str(tmp_path_factory.mktemp('mizpah-user-config')))
