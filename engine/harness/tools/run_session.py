#!/usr/bin/env python3
"""Launch, resume, inspect or export the focused worker/controller harness."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cg.bp_focused_agent_session_python.src.focused_agent_session import (
    ControllerSettings, EndpointConfig, FocusedSession, ModelClient, ReviewPolicy,
    SandboxedShell, SessionPolicy, SessionSettings, ShellConfig, ShellLimits,
    llama_model_client,
)


def bindings(config, root):
    def observe(kind, value):
        if kind == 'model_response' and value['purpose'] in ('worker', 'controller', 'handoff'):
            print(json.dumps(dict(event='model_response', role=value['purpose'],
                status=value['status'], seconds=value['elapsed_seconds'], error=value['error'])), file=sys.stderr, flush=True)
    def client(spec):
        endpoint = EndpointConfig(**spec['endpoint'])
        provider = spec.get('provider', 'direct_json')
        if provider == 'llama_client':
            return llama_model_client(endpoint, known_issues=spec.get('known_issues'),
                diagnostic_characters=spec.get('diagnostic_characters', 8192), observer=observe)
        if provider == 'direct_json':
            return ModelClient(endpoint, observer=observe)
        raise ValueError('Unknown model provider: '+provider)
    worker = client(config['worker'])
    review = config.get('controller')
    controller = client(review) if review else None
    scratch = root/'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    shell_config = config['shell'] | dict(scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']))
    return worker, SandboxedShell(ShellConfig(**shell_config)), controller


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'resume', 'status', 'export', 'project'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--assignment', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--project', type=Path, help='Initial controller-only project document for a new session')
    parser.add_argument('--initial-workspace', type=Path, help='Explicit worker-visible tar archive')
    parser.add_argument('--pause-after', type=int, help='Pause after this many additional completed worker turns')
    parser.add_argument('--output', type=Path, help='New destination tar for export')
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text())
    root = args.root.resolve()
    worker, shell, controller = bindings(config, root)
    if args.action == 'start':
        if args.assignment is None:
            parser.error('start requires --assignment')
        reference = args.reference.read_text() if args.reference else None
        c = config.get('controller')
        review = None
        if reference is not None:
            if c is None:
                parser.error('--reference requires controller settings in --config')
            policy = (config_path.parent/c['policy_file']).resolve().read_text()
            review = ControllerSettings(policy, c['generation'], c['context_capacity'], c['output_tokens'],
                c['maximum_model_calls'], c['maximum_tool_calls'], c['maximum_tool_output_characters'],
                c.get('output_headroom_tokens', 1), c.get('input_target_tokens'),
                c.get('recent_review_exchanges', 2), c.get('investigation_budgets'),
                c.get('maximum_document_edits_per_review'))
        # Bounds stay in configuration as a backstop; the prompt teaches a method rather
        # than a number, since a stated figure becomes a target the model aims at.
        settings = SessionSettings(args.assignment.read_text(), config['worker']['system_prompt'], reference,
            config['worker']['generation'], SessionPolicy(**config['session_policy']),
            ReviewPolicy(**config['review_policy']), review, config['review_on_completion'], config['guidance_prefix'],
            maximum_generation_retries=config.get('maximum_generation_retries', 0),
            **{key: config[key] for key in ('worker_tools', 'maximum_tool_argument_characters',
                'maximum_write_characters', 'maximum_edit_characters', 'maximum_read_lines') if key in config})
        seed = args.initial_workspace.read_bytes() if args.initial_workspace else b''
        session = FocusedSession.create(root, settings, worker=worker, shell=shell,
            controller=controller if reference is not None else None, initial_workspace=seed,
            initial_project_document=args.project.read_text() if args.project else '')
    else:
        if any(value is not None for value in (args.assignment, args.reference, args.initial_workspace, args.project)):
            parser.error('saved sessions retain their original assignment, reference and workspace')
        session = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    if args.action == 'project':
        document = session.project_document()
        if args.output is not None:
            with args.output.open('x') as handle:
                handle.write(document)
        else:
            print(document, end='')
        return
    if args.action == 'export':
        if args.output is None:
            parser.error('export requires --output')
        with args.output.open('xb') as handle:
            handle.write(session.workspace())
        result = dict(status='exported', path=str(args.output.resolve()))
    elif args.action == 'status':
        result = session.status()
    else:
        result = session.run(maximum_worker_turns=args.pause_after)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps(dict(status='stopped', error=str(error))), file=sys.stderr)
        raise SystemExit(2)
