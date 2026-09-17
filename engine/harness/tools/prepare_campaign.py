#!/usr/bin/env python3
"""Freeze a campaign from the previous one: fixtures and graders byte-identical, runtime changes declared.

Usage: prepare_campaign.py PREVIOUS_DIR OUT_DIR [freeze]
"""
import hashlib
import importlib.util
import json
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
spec = importlib.util.spec_from_file_location('pilot', ROOT/'tools/focused-harness/pilot.py')
pilot = importlib.util.module_from_spec(spec); spec.loader.exec_module(pilot)

ATTEMPTS = [('drift', False, 60000, 1800), ('drift', True, 60000, 1800),
            ('trajectory', True, 60000, 3600), ('trajectory', False, 60000, 3600),
            ('continuity', True, 90000, 4500), ('continuity', True, 60000, 4500)]
CHANGES = json.loads((OUT.parent/(OUT.name+'.changes.json')).read_text()) if (OUT.parent/(OUT.name+'.changes.json')).exists() else []


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(exist_ok=False)
    for folder in ('fixtures', 'private'):
        shutil.copytree(PREVIOUS/folder, OUT/folder)
    previous_manifest = json.loads((PREVIOUS/'fixture-manifest.json').read_text())
    files = {name: sha(OUT/name) for name in previous_manifest['files']}
    assert files == previous_manifest['files'], 'Fixtures or graders differ from v4'
    (OUT/'fixture-manifest.json').write_text(json.dumps(dict(cases=previous_manifest['cases'], files=files,
        copied_from=str(PREVIOUS)), indent=2)+'\n')
    (OUT/'configs').mkdir()
    for name, enabled, threshold, deadline in ATTEMPTS:
        identity = f'{name}-'+('on' if enabled else 'off')+f'-{threshold}'
        pilot.save(OUT/'configs'/(identity+'.json'), pilot.configuration(threshold))
    example = json.loads((ROOT/'tools/focused-harness/config.example.json').read_text())
    plan = dict(status='authorized_preflight', authorization='User requested: run '+OUT.name,
        authorized_at=datetime.now(timezone.utc).isoformat(), source_root=str(ROOT), campaign_root=str(OUT),
        comparison_source=str(PREVIOUS),
        attempts=[dict(id=f'{n}-'+('on' if e else 'off')+f'-{t}', controller=e, threshold=t, deadline_seconds=d,
                       config=f'configs/{n}-'+('on' if e else 'off')+f'-{t}.json', changes_from_previous=CHANGES)
                  for n, e, t, d in ATTEMPTS],
        outer_deadline_seconds=21600,
        primary_outcome='accepted_outcome under unchanged functional, completion and continuity protocol checks',
        secondary_outcomes=['private checks', 'worker/controller/handoff tokens and wall time',
            'rejected tool calls, tool errors, read/write/edit use, turn counts and total worker tokens',
            'skeleton-then-fill behavior: write sizes, edit sizes, edits per file, scope reductions after rejection',
            'compaction count and handoff quality', 'controller review sizes and cache hit rate'],
        runtime_changes=CHANGES,
        attribution='Three bundled changes. Outcome attribution at one seed is not possible (v3/v4 showed the '
                    'trajectory delivery failure swapping arms on identical settings). Mechanical metrics are '
                    'attributed per change as listed in secondary_outcomes.',
        worker_system_prompt_sha256=hashlib.sha256(example['worker']['system_prompt'].encode()).hexdigest(),
        comparison_limit='One fresh attempt per arm with seed 731. Model variability remains. The continuity fixture '
                         'mandates unfiltered 40K packet reads and is a compaction stress test, not a benchmark of bounded actions.')
    (OUT/'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    print(json.dumps(dict(campaign=str(OUT), attempts=len(ATTEMPTS)), indent=2))


def freeze_sources() -> None:
    """After qualification: hash and snapshot the sources the campaign runs with."""
    folders = ['cg/backend_async_job_runner_python', 'cg/backend_persistent_model_session_python',
        'cg/bp_focused_agent_session_python', 'cg/data_session_event_log_python', 'cg/infra_revision_store_python',
        'cg/infra_sandboxed_shell_execution_python', 'cg/logic_llamaclient_python',
        'cg/universal_context_payload_projection_python', 'cg/universal_controller_progress_python']
    paths = [p for folder in folders for p in (ROOT/folder).rglob('*')
             if p.is_file() and not any(part in ('__pycache__', '.pytest_cache', '.venv') or part.endswith('.egg-info') for part in p.parts)]
    paths += [ROOT/'tools/focused-harness'/name for name in ('config.example.json', 'config.gemma.json',
        'controller.md', 'pilot.py', 'prepare_pilot.py', 'prepare_campaign.py', 'qualify.py', 'qualify_client_live.py', 'run_session.py')]
    paths += [ROOT/'artifacts/focused-harness/gemma4-gpu0/model-binding.json', OUT/'plan.json',
              OUT/'qualification/result.json'] + sorted((OUT/'configs').iterdir())
    manifest = dict(frozen_at=datetime.now(timezone.utc).isoformat(), previous_campaign=str(PREVIOUS),
        source_snapshot='source-snapshot.tar.gz', runtime_changes=CHANGES,
        files={str(p): sha(p) for p in sorted(paths)})
    (OUT/'source-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    with tarfile.open(OUT/'source-snapshot.tar.gz', 'w:gz') as archive:
        for p in sorted(paths):
            archive.add(p, arcname=str(p.relative_to(ROOT)) if ROOT in p.parents else p.name)
    print(json.dumps(dict(files=len(paths), snapshot=str(OUT/'source-snapshot.tar.gz')), indent=2))


if __name__ == '__main__':
    freeze_sources() if len(sys.argv) > 3 and sys.argv[3] == 'freeze' else main()
