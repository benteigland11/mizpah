"""The seats' prompts compose from prompts/: shared pieces plus the seat's own, in order.txt's order; the
reviewer from its explicit list; the deputy from its own folder, shared names looked up above it; a piece not yet finished from wip/."""
from pathlib import Path

from mizpah import prompts


def _folder(tmp_path: Path) -> Path:
    (tmp_path/'wip').mkdir()
    (tmp_path/'order.txt').write_text('glossary\nprobes\npolicy\n')
    (tmp_path/'order_reviewer.txt').write_text('glossary\nprobes_reviewer\n')
    (tmp_path/'deputy').mkdir()
    (tmp_path/'deputy'/'order.txt').write_text('glossary\nglossary_deputy\nprobes\n')
    (tmp_path/'deputy'/'glossary_deputy.md').write_text('- d\n')
    (tmp_path/'deputy'/'probes.md').write_text('## P for the deputy\n')
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


def test_a_listed_seat_takes_only_its_list(tmp_path):
    f = _folder(tmp_path)
    assert prompts.compose('reviewer', f) == '# G\n\n- r\n'
    # The deputy's folder first, then the shared one: its own probes.md shadows the loop's; the glossary comes from above.
    assert prompts.compose('deputy', f) == '# G\n\n- d\n\n## P for the deputy\n'
    assert prompts.pieces_of('deputy', f) == [('glossary', False), ('glossary_deputy', False), ('probes', False)]


def test_a_boundary_message_is_read_by_name_and_filled(tmp_path):
    (tmp_path/'messages').mkdir()
    (tmp_path/'messages'/'effort_worker.md').write_text('bucket $bucket, $turns turns, block $task\n')
    prompts.set_messages_dir(tmp_path)
    assert prompts.message('effort_worker', bucket='low', turns=61, task='x') == 'bucket low, 61 turns, block x\n'
    assert prompts.message('effort_worker', bucket='low') == 'bucket low, $turns turns, block $task\n'   # a missing field stays visible, never raises
