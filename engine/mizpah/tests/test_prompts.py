"""The seats' prompts compose from prompts/: shared pieces plus the seat's own, in order.txt's order; the
reviewer from its explicit list; a piece not yet finished from wip/."""
from pathlib import Path

from mizpah import prompts


def _folder(tmp_path: Path) -> Path:
    (tmp_path/'wip').mkdir()
    (tmp_path/'order.txt').write_text('glossary\nprobes\npolicy\n')
    (tmp_path/'order_reviewer.txt').write_text('glossary\nprobes_reviewer\n')
    (tmp_path/'glossary.md').write_text('# G\n')
    (tmp_path/'probes.md').write_text('## P\n')
    (tmp_path/'probes_worker.md').write_text('- w\n')
    (tmp_path/'probes_reviewer.md').write_text('- r\n')
    (tmp_path/'wip'/'policy_worker.md').write_text('## W\n')
    return tmp_path


def test_a_seat_takes_shared_then_its_own_in_order(tmp_path):
    f = _folder(tmp_path)
    assert prompts.compose('worker', f) == '# G\n\n## P\n\n- w\n\n## W\n'
    assert prompts.compose('controller', f) == '# G\n\n## P\n'          # no controller pieces beyond the shared
    assert prompts.pieces_of('worker', f) == [('glossary', False), ('probes', False), ('probes_worker', False), ('policy_worker', True)]


def test_the_reviewer_takes_only_its_list(tmp_path):
    f = _folder(tmp_path)
    assert prompts.compose('reviewer', f) == '# G\n\n- r\n'
