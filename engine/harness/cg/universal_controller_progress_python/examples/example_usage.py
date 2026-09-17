"""Persist a direction correction separately from a short observation window."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.controller_progress import ControllerProgress, ReviewPolicy

controller = ControllerProgress(ReviewPolicy(20, 10, 1, 2000, 500, 20000))
controller.edit_document(0, '', '# Export project\n- [x] Verify column order\n- [ ] Preserve row selection\n- [ ] Test empty views\n')
controller.observe(dict(turn=1, response='The export omits active filters.'))
assert controller.due()
controller.accept(json.dumps(dict(correction='Preserve the active filter in the export.',
    evidence='The observed output included hidden rows.', warrant='The reference requires the visible view.')))
restored = ControllerProgress.from_state(controller.export_state())
assert restored.applied_guidance() == controller.applied_guidance()
page = restored.read_document(0, 2000)
start = page['text'].index('- [ ] Test empty views')
restored.edit_range(page['revision'], start, start+len('- [ ] Test empty views'),
                    '- [ ] Verify empty and filtered views')
assert '- [x] Verify column order' in restored.document
print(restored.document)
