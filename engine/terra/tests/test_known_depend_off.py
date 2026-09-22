"""`known depend --off` withdraws a dependency declared by mistake; a file dep under a hidden directory is refused."""
from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import add_dependency, remove_dependency
from test_known_graph import _mk_known


def test_off_withdraws_and_hidden_on_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "k1")
    (tmp_path/"piece.pdf").write_text("%PDF")
    (tmp_path/".tool-output").mkdir()
    (tmp_path/".tool-output"/"log").write_text("ok")
    rec = add_dependency(tmp_path, "k1", ["file:piece.pdf"])
    assert [d["path"] for d in rec["deps"]["files"]] == ["piece.pdf"]
    with pytest.raises(ValueError, match="hidden directory"):
        add_dependency(tmp_path, "k1", ["file:.tool-output/log"])
    rec = remove_dependency(tmp_path, "k1", ["file:piece.pdf"])
    assert rec["deps"]["files"] == []
    with pytest.raises(ValueError, match="does not depend"):
        remove_dependency(tmp_path, "k1", ["file:piece.pdf"])
