#!/usr/bin/env python3
"""Scenario-specific qualification, timed paired execution and terminal grading."""
import argparse
import asyncio
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from cg.backend_async_job_runner_python.src.async_job_runner import JobRunner, JobSpec
from cg.bp_focused_agent_session_python.src.focused_agent_session import (
    FocusedSession, SessionEventLog, read_workspace_file, write_workspace_file)
from cg.infra_revision_store_python.src.revision_store import RevisionStore
spec=importlib.util.spec_from_file_location('launcher',Path(__file__).with_name('run_session.py'))
launcher=importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
OUT=ROOT/'artifacts/focused-harness/gemma4-pilot-v2-20260916'
MODEL=ROOT/'artifacts/focused-harness/gemma4-gpu0/model-binding.json'

def stamp(): return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,default=str)+'\n'); temporary.replace(path)
def http(path,body=None,port=58081,timeout=30):
    request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
        data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response: return json.load(response)

def configuration(threshold):
    config=json.loads((ROOT/'tools/focused-harness/config.example.json').read_text())
    binding=json.loads(MODEL.read_text())
    for role in ['worker','controller']:
        config[role]['endpoint']=binding['endpoint']
        config[role]['generation']=binding['generation']|dict(seed=731)
    config['controller'].update(context_capacity=131072,output_tokens=-1,output_headroom_tokens=12000,
        policy_file=str(ROOT/'tools/focused-harness/controller.md'))
    config['session_policy'].update(context_capacity=131072,rollover_threshold=threshold,
        worker_output_tokens=-1,handoff_output_tokens=-1,output_headroom_tokens=24000,
        handoff_generation_overrides=dict(reasoning_budget_tokens=2048),overflow_excerpt_characters=16000)
    config['shell']['limits'].update(visible_output_bytes=262144,output_bytes=1048576,
        memory_bytes=1073741824,workspace_bytes=134217728,temporary_bytes=67108864,
        command_seconds=90,shutdown_seconds=5)
    return config

def grade(name,workspace,root):
    config=configuration(60000)
    _,shell,_=launcher.bindings(config,root)
    grader=(OUT/'private'/(name+'-grader.py')).read_bytes()
    temporary=write_workspace_file(workspace,'.private-evaluate.py',grader,
        byte_limit=shell.config.limits.workspace_bytes,file_limit=shell.config.limits.max_files)
    result=shell.run('python .private-evaluate.py',temporary)
    record={k:v for k,v in asdict(result).items() if k!='workspace'}
    try:
        record['grade']=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        record['grade']=dict(all_pass=False,error='Grader did not emit its expected result')
    save(root/'grade.json',record)
    return record

def checkpoint_retention(root,events,packet_reads,packets):
    """Audit actual saved checkpoint files between the constructed packet reads."""
    if not packet_reads: return []
    facts={n:re.search(r'customer alias (\S+) must map to canonical customer (\S+)\.',text).groups()
        for n,text in packets.items()}
    snapshots={}
    for event in events:
        if event.event_type!='checkpoint' or 'state' not in event.payload: continue
        state=event.payload['state']; turn=state['controller_progress']['turns']
        if not turn or turn in snapshots or state['active_turn'] is not None: continue
        digest=state['workspace']; assert re.fullmatch('[0-9a-f]{64}',digest)
        source=root/'workspaces'/(digest+'.sqlite3'); assert source.is_file()
        workspace=base64.b64decode(RevisionStore(source).read()['data']['content'],validate=True)
        assert hashlib.sha256(workspace).hexdigest()==digest
        limits=state['shell_config']['limits']
        try:
            text=read_workspace_file(workspace,'CHECKPOINT.md',byte_limit=limits['workspace_bytes'],
                file_limit=limits['max_files']).decode('utf-8')
        except FileNotFoundError:
            text=''
        snapshots[turn]=text
    audit=[]
    for index,read in enumerate(packet_reads):
        end=packet_reads[index+1]['turn'] if index+1<len(packet_reads) else max(snapshots,default=0)+1
        before=snapshots.get(read['turn'],'')
        candidates=[(turn,text) for turn,text in sorted(snapshots.items()) if read['turn']<turn<end and text!=before]
        evidence=next(((turn,text) for turn,text in candidates
            if all(alias in text and canonical in text for n,(alias,canonical) in facts.items() if n<=read['packet'])),None)
        audit.append(dict(packet=read['packet'],read_turn=read['turn'],
            checkpoint_turn=evidence[0] if evidence else None,
            accumulated_fact_strings_retained=evidence is not None,
            checkpoint_sha256=hashlib.sha256(evidence[1].encode()).hexdigest() if evidence else None))
    return audit

def qualify_checkpoint_retention(path):
    path.mkdir(parents=True,exist_ok=False)
    packets={1:'customer alias C001 must map to canonical customer account-a.',
             2:'customer alias C002 must map to canonical customer account-b.'}
    reads=[dict(packet=1,turn=1),dict(packet=2,turn=3)]
    controls={}
    for label,last in [('retained','Packet 1: C001 account-a\nPacket 2: C002 account-b\n'),
                       ('overwritten','Packet 2: C002 account-b\n')]:
        root=path/label; events=[]
        for turn,text in enumerate(['','Packet 1: C001 account-a\n','Packet 1: C001 account-a\n',last],1):
            workspace=write_workspace_file(b'','CHECKPOINT.md',text.encode(),byte_limit=1000000,file_limit=100)
            digest=hashlib.sha256(workspace).hexdigest()
            store=RevisionStore(root/'workspaces'/(digest+'.sqlite3'))
            if not store.read()['revision']:
                store.commit(0,lambda _:dict(content=base64.b64encode(workspace).decode()))
            events.append(SimpleNamespace(event_type='checkpoint',payload=dict(state=dict(
                controller_progress=dict(turns=turn),active_turn=None,workspace=digest,
                shell_config=dict(limits=dict(workspace_bytes=1000000,max_files=100))))))
        controls[label]=checkpoint_retention(root,events,reads,packets)
    assert all(x['accumulated_fact_strings_retained'] for x in controls['retained'])
    assert controls['overwritten'][0]['accumulated_fact_strings_retained']
    assert not controls['overwritten'][1]['accumulated_fact_strings_retained']
    result=dict(passed=True,controls=controls,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    save(path/'result.json',result)
    return result

def metrics(root):
    totals={}
    events=SessionEventLog(root/'events').read_strict('session')
    for event in events:
        p=event.payload
        if event.event_type!='model_response' or p['purpose'] not in ['worker','controller','handoff']: continue
        try: response=json.loads(p['body'])
        except (ValueError,KeyError): continue
        if 'usage' not in response: continue
        role=totals.setdefault(p['purpose'],dict(calls=0,seconds=0,prompt_tokens=0,completion_tokens=0,
            reported_cache_tokens=0,cache_reporting_calls=0,prompt_eval_ms=0,prompt_eval_reporting_calls=0))
        role['calls']+=1; role['seconds']+=p['elapsed_seconds']
        role['prompt_tokens']+=response['usage'].get('prompt_tokens',0)
        role['completion_tokens']+=response['usage'].get('completion_tokens',0)
        detail=response['usage'].get('prompt_tokens_details',{})
        if 'cached_tokens' in detail:
            role['reported_cache_tokens']+=detail['cached_tokens']; role['cache_reporting_calls']+=1
        if 'prompt_ms' in response.get('timings',{}):
            role['prompt_eval_ms']+=response['timings']['prompt_ms']; role['prompt_eval_reporting_calls']+=1
    packet_reads=[]
    with tarfile.open(OUT/'fixtures/continuity/workspace.tar') as archive:
        packets={n:archive.extractfile(f'packets/{n:02d}.log').read().decode() for n in range(1,13)}
    for event in events:
        if event.event_type!='worker_turn': continue
        calls=event.payload['response'].get('tool_calls',[])
        for call in calls:
            try:
                command=json.loads(call['function']['arguments']).get('command','').strip()
                match=re.fullmatch(r'python inspect_packet\.py ([1-9]|1[0-2])',command)
                if not match: continue
                n=int(match[1]); result=next(r['result'] for r in event.payload['tool_results'] if r['call_id']==call['id'])
                packet_reads.append(dict(packet=n,turn=event.payload['turn'],single_call=len(calls)==1,
                    full_output=result.get('stdout')==packets[n],successful=result.get('exit_code')==0))
            except (ValueError,KeyError,StopIteration): continue
    handoffs=sum(e.event_type=='worker_handoff' for e in events)
    review_turns=[e.payload['turn'] for e in events if e.event_type=='controller_review']
    checkpoint_audit=checkpoint_retention(root,events,packet_reads,packets)
    checkpoint_coverage=(len(checkpoint_audit)==12 and all(p['accumulated_fact_strings_retained'] for p in checkpoint_audit))
    exposure=(len(packet_reads)==12 and [p['packet'] for p in packet_reads]==list(range(1,13))
        and all(p['single_call'] and p['full_output'] and p['successful'] for p in packet_reads)
        and all(b['turn']-a['turn']>=2 for a,b in zip(packet_reads,packet_reads[1:]))
        and handoffs>=2 and 21 in review_turns)
    return dict(roles=totals,review_turns=review_turns,
        review_decisions=[e.payload for e in events if e.event_type=='controller_review'],
        packet_reads=packet_reads,
        repeated_packet_reads=len(packet_reads)-len({p['packet'] for p in packet_reads}),
        continuity_exposure=exposure, checkpoint_audit=checkpoint_audit,
        checkpoint_retention_coverage=checkpoint_coverage,
        continuity_coverage=exposure and checkpoint_coverage,
        handoffs=handoffs,
        archived_windows=sum(e.event_type=='worker_history_archive' for e in events),
        projections=sum(e.event_type=='context_projection' for e in events),
        controller_projections=sum(e.event_type in ('controller_evidence_projection','controller_context_projection') for e in events),
        controller_context_projections=[e.payload for e in events if e.event_type=='controller_context_projection'],
        controller_decision_rejections=sum(e.event_type=='controller_decision_rejected' for e in events),
        controller_generation_rejections=sum(e.event_type=='controller_generation_rejected' for e in events),
        project_updates=[{k:e.payload[k] for k in ('turn','revision','characters')} for e in events if e.event_type=='project_edit'],
        project_edit_errors=sum(e.event_type=='controller_tool' and e.payload['name'].startswith('project_edit')
            and e.payload['result'].get('status')=='error' for e in events))

def cleanup(root):
    # systemd-owned sandbox commands have their own service groups. Select only
    # units whose explicit input directory belongs to this attempt.
    listing=subprocess.run(['systemctl','--user','list-units','isolated-shell-*','--all','--output=json'],
        capture_output=True,text=True,timeout=15,check=True)
    stopped=[]
    for unit in json.loads(listing.stdout):
        name=unit['unit']
        command=subprocess.run(['systemctl','--user','show',name,'--property=ExecStart','--value'],
            capture_output=True,text=True,timeout=10,check=True).stdout
        if str(root) in command:
            subprocess.run(['systemctl','--user','stop',name],capture_output=True,timeout=15,check=True)
            stopped.append(name)
    # A single terminal cleanup check, not trajectory polling. Allow socket
    # disconnect cancellation to reach the inference worker before checking.
    time.sleep(3)
    slots=http('/slots')
    idle=all(not slot.get('is_processing',True) for slot in slots)
    result=dict(stopped_shell_units=stopped,slots=slots,inference_idle=idle)
    if not idle:
        binding=json.loads(MODEL.read_text())
        # A replaced process is not this campaign's owned inference service.
        cmdline=Path('/proc')/str(binding['managed_pid'])/'cmdline'
        assert cmdline.exists() and binding['generation']['model'] in cmdline.read_bytes().decode().replace('\0',' '), 'Owned server process changed'
        result['managed_stop']=http('/stop?port=58081',{},port=58000)
        result['owned_pid_stopped']=not cmdline.exists()
        assert result['owned_pid_stopped'], 'Managed stop did not remove the owned server process'
        result['action']='Stopped only the campaign endpoint; remaining attempts must not run.'
    save(root/'cleanup.json',result)
    return result

def finalize():
    """Service watchdog cleanup if the campaign cannot write its terminal marker."""
    state_path=OUT/'campaign-state.json'
    if not state_path.exists() or (OUT/'campaign-terminal.json').exists(): return
    state=json.loads(state_path.read_text())
    active=state.get('active')
    if active:
        state['watchdog_cleanup']=cleanup(OUT/'attempts'/active['id']/'session')
    state.update(status='supervisor_stopped',finished_at=stamp())
    save(OUT/'campaign-state.json',state); save(OUT/'campaign-terminal.json',state)

async def job(argv,root,seconds):
    runner=JobRunner(max_output_bytes=8_000_000,termination_grace_seconds=2)
    submitted=await runner.submit(JobSpec(argv=argv,cwd=str(ROOT),timeout_seconds=seconds))
    result=await runner.wait(submitted.job_id)
    record={k:v for k,v in asdict(result).items() if k not in ['stdout','stderr']}
    root.mkdir(parents=True,exist_ok=True)
    (root/'stdout.log').write_bytes(result.stdout); (root/'stderr.log').write_bytes(result.stderr)
    save(root/'job.json',record)
    return record

def command(name,enabled,config,root):
    fixture=OUT/'fixtures'/name
    argv=[sys.executable,str(ROOT/'tools/focused-harness/run_session.py'),'start',
        '--config',str(config),'--root',str(root),'--assignment',str(fixture/'assignment.txt'),
        '--initial-workspace',str(fixture/'workspace.tar')]
    if enabled: argv+=['--reference',str(fixture/'reference.txt')]
    return argv

async def qualify(reuse_cancellation=None):
    path=OUT/'qualification'; path.mkdir(exist_ok=False)
    qualify_checkpoint_retention(path/'checkpoint-metrics')
    results={}
    for name in ['drift','trajectory','continuity']:
        results[name]={}
        for label,source in [('invalid',OUT/'fixtures'/name/'workspace.tar'),
                             ('oracle',OUT/'private'/(name+'-oracle.tar'))]:
            result=grade(name,source.read_bytes(),path/(name+'-'+label))
            results[name][label]=result['grade']
        assert results[name]['oracle']['all_pass'] and not results[name]['invalid']['all_pass']
    with tarfile.open(OUT/'fixtures/continuity/workspace.tar') as archive:
        packet=archive.extractfile('packets/01.log').read().decode()
    count=len(http('/tokenize',dict(content=packet,add_special=False,parse_special=True))['tokens'])
    assert 18000<count<26000
    results['packet_tokens']=count
    save(path/'graders.json',results)
    # Deliberately stall a live native request. The independent job timer ends
    # its client; prove the dedicated inference slot actually becomes idle.
    binding=json.loads(MODEL.read_text())
    payload=binding['generation']|dict(max_tokens=-1,reasoning_budget_tokens=32,
        messages=[dict(role='user',content='Print the integers from 1 to 100000, one per line, without abbreviating or skipping.')])
    save(path/'cancellation-request.json',payload)
    cancelcode="import json,urllib.request; from pathlib import Path; p=Path(__import__('sys').argv[1]); r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:58081/v1/chat/completions',data=p.read_bytes(),headers={'Content-Type':'application/json'}),timeout=900); print(r.read().decode())"
    if reuse_cancellation is not None:
        previous=Path(reuse_cancellation).resolve()
        assert json.loads((previous/'cancellation-request.json').read_text())==payload
        assert json.loads((previous/'cancellation/job.json').read_text())['status']=='timed_out'
        assert any(s.get('is_processing') for s in json.loads((previous/'cancellation/slots-before-deadline.json').read_text()))
        assert json.loads((previous/'cancellation/cleanup.json').read_text())['inference_idle']
        save(path/'reused-cancellation.json',dict(source=str(previous),
            files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (previous/'cancellation').iterdir() if p.is_file()}))
    else:
        pending=asyncio.create_task(job([sys.executable,'-c',cancelcode,str(path/'cancellation-request.json')],path/'cancellation',2))
        await asyncio.sleep(0.5)
        active_slots=http('/slots')
        save(path/'cancellation'/'slots-before-deadline.json',active_slots)
        outcome=await pending
        assert any(slot.get('is_processing') for slot in active_slots)
        assert outcome['status']=='timed_out'
        assert cleanup(path/'cancellation')['inference_idle']
    # One small live session checks worker tools, controller document/decision,
    # durable workspace and native completion before the frozen task trials.
    config=path/'config.json'; save(config,configuration(60000))
    assignment=path/'assignment.txt'; assignment.write_text('Write proof.txt containing exactly FOCUSED_LOOP_READY, read it back with bash and report the observed result.\n')
    reference=path/'reference.txt'; reference.write_text(assignment.read_text())
    root=path/'live-session'
    argv=[sys.executable,str(ROOT/'tools/focused-harness/run_session.py'),'start','--config',str(config),
        '--root',str(root),'--assignment',str(assignment),'--reference',str(reference)]
    outcome=await job(argv,path/'live-job',300)
    assert cleanup(root)['inference_idle']
    assert outcome['status']=='succeeded', outcome
    bindings=launcher.bindings(configuration(60000),root)
    session=FocusedSession.open(root,worker=bindings[0],shell=bindings[1],controller=bindings[2])
    assert session.status()['status']=='complete' and session.status()['controller_reviews']>=1
    from cg.bp_focused_agent_session_python.src.focused_agent_session import read_workspace_file
    assert read_workspace_file(session.workspace(),'proof.txt',byte_limit=134217728,file_limit=10000).decode().strip()=='FOCUSED_LOOP_READY'
    save(path/'result.json',dict(passed=True,completed_at=stamp(),graders=results,
        live_status=session.status(),live_metrics=metrics(root)))
    print(json.dumps(dict(passed=True,path=str(path)),indent=2))

async def campaign():
    assert json.loads((OUT/'qualification/result.json').read_text())['passed']
    assert json.loads((OUT/'qualification/checkpoint-metrics/result.json').read_text())['passed']
    manifest=json.loads((OUT/'fixture-manifest.json').read_text())
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((OUT/name).read_bytes()).hexdigest()==digest,name
    sources=json.loads((OUT/'source-manifest.json').read_text())
    assert not (OUT/'campaign-state.json').exists(), 'Campaign cannot be replayed'
    order=[('drift',False,60000,1800),('drift',True,60000,1800),
        ('trajectory',True,60000,3600),('trajectory',False,60000,3600),
        ('continuity',True,90000,4500),('continuity',True,60000,4500)]
    state=dict(started_at=stamp(),status='running',planned_attempts=6,completed=[])
    save(OUT/'campaign-state.json',state)
    try:
        for name,enabled,threshold,deadline in order:
            for filename,digest in sources['files'].items():
                assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==digest,filename
            binding=json.loads(MODEL.read_text())
            managed=http('/status/58081',port=58000)
            assert managed['running'] and managed['pid']==binding['managed_pid'] and managed['model']==binding['generation']['model'], 'Inference binding changed'
            assert http('/health')['status']=='ok'
            assert all(not s.get('is_processing',True) for s in http('/slots')), 'Shared inference is busy'
            identity=f'{name}-'+('on' if enabled else 'off')+f'-{threshold}'
            root=OUT/'attempts'/identity; root.mkdir(parents=True,exist_ok=False)
            config=root/'config.json'; save(config,configuration(threshold))
            session_root=root/'session'
            state['active']=dict(id=identity,started_at=stamp(),deadline_seconds=deadline)
            save(OUT/'campaign-state.json',state)
            result=await job(command(name,enabled,config,session_root),root,deadline)
            cleaned=cleanup(session_root)
            bindings=launcher.bindings(configuration(threshold),session_root)
            try:
                session=FocusedSession.open(session_root,worker=bindings[0],shell=bindings[1],
                    controller=bindings[2] if enabled else None)
                snapshot=session.workspace(); (root/'final-workspace.tar').write_bytes(snapshot)
                status=session.status(); (root/'project.md').write_text(session.project_document())
                scored=grade(name,snapshot,root/'grading')['grade']
                measured=metrics(session_root)
            except Exception as error:
                status=dict(status='unreadable',error=repr(error)); scored=dict(all_pass=False); measured={}
            report=dict(id=identity,case=name,controller_enabled=enabled,threshold=threshold,
                job=result,status=status,grade=scored,metrics=measured,cleanup=cleaned,
                accepted_outcome=(result['status']=='succeeded' and status['status']=='complete'
                    and scored['all_pass'] and (name!='continuity' or measured.get('continuity_coverage',False))))
            save(root/'result.json',report)
            state['completed'].append(dict(id=identity,job_status=result['status'],
                session_status=status['status'],all_functional_checks=scored['all_pass'],
                accepted_outcome=report['accepted_outcome']))
            save(OUT/'campaign-state.json',state)
            if not cleaned['inference_idle']: raise RuntimeError('Inference cancellation required a managed stop')
        state['status']='complete'
    except BaseException as error:
        state.update(status='stopped',error=repr(error))
        raise
    finally:
        state.update(finished_at=stamp(),active=None)
        save(OUT/'campaign-state.json',state)
        save(OUT/'campaign-terminal.json',state)
    print(json.dumps(state,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['qualify','run','finalize'])
    parser.add_argument('--reuse-cancellation',type=Path)
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args=parser.parse_args()
    OUT=args.output_dir.resolve()
    if args.action=='finalize': finalize()
    else: asyncio.run(qualify(args.reuse_cancellation) if args.action=='qualify' else campaign())
