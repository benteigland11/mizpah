"""The Deputy's notices: one line each, latest per number; read into the app's tray as NOTICE FROM THE DEPUTY."""
from __future__ import annotations

import json
from pathlib import Path
from mizpah import notices


def test_a_notice_is_one_line_in_the_apps_shape_and_a_resend_is_a_revision(tmp_path: Path) -> None:
    first = notices.send(tmp_path, 'DN-x', 'One thing.', [('Why', [notices.line('because', lead='Reason')])], status='FYI')
    assert first['kind'] == 'deputyNotice' and first['from'] == 'The Deputy' and first['status'] == 'FYI'
    assert first['sections'] == [dict(heading='Why', lines=[dict(text='because', lead='Reason')])]
    assert (tmp_path/'notices.jsonl').read_text().count('\n') == 1
    notices.send(tmp_path, 'DN-y', 'Another.', [])
    notices.send(tmp_path, 'DN-x', 'One thing, revised.', [], hot=True)
    on_file = notices.read(tmp_path)
    assert [d['number'] for d in on_file] == ['DN-x', 'DN-y']                 # first-sent order, one per number
    assert on_file[0]['title'] == 'One thing, revised.' and on_file[0]['hot']   # the latest wins
    assert all(json.loads(l) for l in (tmp_path/'notices.jsonl').read_text().splitlines())
