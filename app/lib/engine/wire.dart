/// The seam on the wire: every type an [Engine] or [ProviderClient] hands
/// across, as JSON and back. The server encodes, the remote client decodes;
/// both sides use exactly these functions so the shapes cannot drift.
/// Dates travel as ISO-8601 UTC; enums by name.
library;

import '../models/budget.dart';
import '../models/databook.dart';
import '../models/document.dart';
import '../models/loop.dart';
import '../models/procedure.dart';
import '../models/provider.dart';
import '../models/route.dart';
import 'engine.dart';
import 'procedure_history.dart';
import 'run_floor.dart';
import '../models/storage.dart';

typedef Json = Map<String, dynamic>;

String? _dt(DateTime? d) => d?.toUtc().toIso8601String();
DateTime? _dtOf(Object? v) => v is String ? DateTime.tryParse(v) : null;
List<String> _strs(Object? v) => (v as List? ?? const []).map((e) => '$e').toList();
List<Json> _maps(Object? v) => [for (final e in (v as List? ?? const [])) (e as Map).cast<String, dynamic>()];

// ---- briefs, attention, sessions ----------------------------------------

Json briefSummary(BriefSummary b) =>
    {'id': b.id, 'title': b.title, 'path': b.path, 'state': b.state, 'attention': b.attention, 'ended_at': _dt(b.endedAt)};
BriefSummary briefSummaryOf(Json j) => BriefSummary(
      id: j['id'] as String,
      title: j['title'] as String? ?? '',
      path: j['path'] as String? ?? '',
      state: j['state'] as String? ?? 'idle',
      attention: (j['attention'] as num?)?.toInt() ?? 0,
      endedAt: _dtOf(j['ended_at']),
    );

Json attentionItem(AttentionItem a) => {'project': briefSummary(a.project), 'document': a.document.toJson()};
AttentionItem attentionItemOf(Json j) => AttentionItem(
      project: briefSummaryOf((j['project'] as Map).cast()),
      document: InboxDocument.fromJson((j['document'] as Map).cast()),
    );

Json runSession(RunSession s) =>
    {'path': s.path, 'started_at': _dt(s.startedAt), 'running': s.running, 'archived': s.archived, 'stop': s.stop};
RunSession runSessionOf(Json j) => RunSession(
      path: j['path'] as String,
      startedAt: _dtOf(j['started_at']),
      running: j['running'] == true,
      archived: j['archived'] == true,
      stop: j['stop'] as String?,
    );

Json deputyTurn(DeputyTurn t) => {
      'at': t.at.millisecondsSinceEpoch / 1000,
      'role': t.role,
      'text': t.text,
      'showing': t.showing,
      'error': t.error,
      'seconds': t.seconds,
      'tool_calls': t.toolCalls,
    };

// ---- route ---------------------------------------------------------------

Json routeTask(RouteTask t) => {
      'id': t.id,
      'title': t.title,
      'status': t.status,
      'phase': t.phase,
      'enabler_id': t.enablerId,
      'map_id': t.unknownId,
      'bucket': t.bucket,
      'points': t.points,
      'priority': t.priority,
      'deps': t.deps,
      'skill': t.skill,
      'role': t.role,
      'pickable': t.pickable,
      'owner_agent': t.ownerAgent,
      'blocked_reason': t.blockedReason,
      'acceptance': t.acceptance,
      'evidence': [
        {'runs': t.evidenceRuns, 'knowns': t.evidenceKnowns},
      ],
      'updated_at': t.updatedAt,
    };

Json phaseRow(PhaseRow p) => {
      'id': p.id,
      'title': p.title,
      'closed': p.closed,
      'open': p.open,
      'done': p.done,
      'blocked': p.blocked,
      'in_progress': p.inProgress,
      'unreachable': p.unreachable,
      'exit_ready': p.exitReady,
      'points_open': p.pointsOpen,
    };

Json routeStatus(RouteStatus r) => {
      'tasks': [for (final t in r.tasks) routeTask(t)],
      'phases': {'current': r.currentPhase, 'phases': [for (final p in r.phases) phaseRow(p)]},
      'budget': {
        'budget_points': r.budget.budgetPoints,
        'points_plan': r.budget.pointsPlan,
        'points_actual': r.budget.pointsActual,
        'points_done': r.budget.pointsDone,
        'over_budget': r.budget.overBudget,
      },
      'attention': [
        for (final a in r.attention) {'kind': a.kind, 'id': a.id, 'severity': a.severity, 'why': a.why},
      ],
      'plan_locked': r.planLocked,
    };
RouteStatus routeStatusOf(Json j) => RouteStatus.fromJson(j);

Json routeEvent(RouteEvent e) =>
    {'at': e.at, 'kind': e.kind, 'task': e.task, 'title': e.title, 'reason': e.detail, 'runs': e.runs, 'knowns': e.knowns};
RouteEvent routeEventOf(Json j) => RouteEvent.fromJson(j);

// ---- loop ----------------------------------------------------------------

Json controllerAction(ControllerAction a) => {'kind': a.kind, 'at': _dt(a.at), 'summary': a.summary, 'refused': a.refused};
ControllerAction controllerActionOf(Json j) => ControllerAction(
      kind: j['kind'] as String? ?? '',
      at: _dtOf(j['at']) ?? DateTime.fromMillisecondsSinceEpoch(0),
      summary: j['summary'] as String? ?? '',
      refused: _strs(j['refused']),
    );

Json workerSession(WorkerSession w) => {
      'task_id': w.taskId,
      'task_title': w.taskTitle,
      'unknown_id': w.unknownId,
      'bucket': w.bucket,
      'status': w.status,
      'turns': w.turns,
      'budget_turns': w.budgetTurns,
      'handoffs': w.handoffs,
      'gate_ok': w.gateOk,
      'gate_problems': w.gateProblems,
      'note': w.note,
      'last_checkin_turn': w.lastCheckinTurn,
      'last_checkin_verdict': w.lastCheckinVerdict,
      'blocked_reason': w.blockedReason,
    };
WorkerSession workerSessionOf(Json j) => WorkerSession(
      taskId: j['task_id'] as String? ?? '',
      taskTitle: j['task_title'] as String? ?? '',
      unknownId: j['unknown_id'] as String? ?? '',
      bucket: j['bucket'] as String? ?? 'low',
      status: j['status'] as String? ?? 'in_progress',
      turns: (j['turns'] as num?)?.toInt() ?? 0,
      budgetTurns: (j['budget_turns'] as num?)?.toInt() ?? 0,
      handoffs: (j['handoffs'] as num?)?.toInt() ?? 0,
      gateOk: j['gate_ok'] == true,
      gateProblems: _strs(j['gate_problems']),
      note: j['note'] as String? ?? '',
      lastCheckinTurn: (j['last_checkin_turn'] as num?)?.toInt(),
      lastCheckinVerdict: j['last_checkin_verdict'] as String?,
      blockedReason: j['blocked_reason'] as String?,
    );

Json loopState(LoopState s) => {
      'mode': s.mode.name,
      'running': s.running,
      'cycle': s.cycle,
      'tasks_run': s.tasksRun,
      'max_tasks': s.maxTasks,
      'max_cycles': s.maxCycles,
      'stop_reason': s.stopReason,
      'controller': {
        'model': s.controller.model,
        'endpoint': s.controller.endpoint,
        'recent': [for (final a in s.controller.recent) controllerAction(a)],
        'next': s.controller.next,
        'held_guidance': s.controller.heldGuidance,
      },
      'workers': [for (final w in s.workers) workerSession(w)],
    };
LoopState loopStateOf(Json j) {
  final c = (j['controller'] as Map?)?.cast<String, dynamic>() ?? const {};
  return LoopState(
    mode: LoopMode.values.firstWhere((m) => m.name == j['mode'], orElse: () => LoopMode.hold),
    running: j['running'] == true,
    cycle: (j['cycle'] as num?)?.toInt() ?? 0,
    tasksRun: (j['tasks_run'] as num?)?.toInt() ?? 0,
    maxTasks: (j['max_tasks'] as num?)?.toInt() ?? 0,
    maxCycles: (j['max_cycles'] as num?)?.toInt() ?? 0,
    stopReason: j['stop_reason'] as String?,
    controller: ControllerState(
      model: c['model'] as String? ?? '',
      endpoint: c['endpoint'] as String? ?? '',
      recent: [for (final a in _maps(c['recent'])) controllerActionOf(a)],
      next: c['next'] as String? ?? '',
      heldGuidance: c['held_guidance'] as String?,
    ),
    workers: [for (final w in _maps(j['workers'])) workerSessionOf(w)],
  );
}

Json turn(Turn t) => {
      'n': t.n,
      'kind': t.kind,
      'at': _dt(t.at),
      'title': t.title,
      'body': t.body,
      'ok': t.ok,
      'before': t.before,
      'after': t.after,
      if (t.tone.isNotEmpty) 'tone': t.tone,
      if (t.anchor != 'center') 'anchor': t.anchor,
      if (t.lang != null) 'lang': t.lang,
      if (t.resultLang != null) 'result_lang': t.resultLang,
    };
Turn turnOf(Json j) => Turn(
      n: (j['n'] as num?)?.toInt() ?? 0,
      kind: j['kind'] as String? ?? '',
      at: _dtOf(j['at']) ?? DateTime.fromMillisecondsSinceEpoch(0),
      title: j['title'] as String? ?? '',
      body: j['body'] as String? ?? '',
      ok: j['ok'] != false,
      before: j['before'] as String?,
      after: j['after'] as String?,
      tone: j['tone'] as String? ?? '',
      anchor: j['anchor'] as String? ?? 'center',
      lang: j['lang'] as String?,
      resultLang: j['result_lang'] as String?,
    );

Json floorSession(FloorSession s) =>
    {'id': s.id, 'title': s.title, 'status': s.status, 'turns': s.turns, 'started_at': _dt(s.startedAt), 'phase': s.phase};
FloorSession floorSessionOf(Json j) => FloorSession(
      id: j['id'] as String,
      title: j['title'] as String? ?? '',
      status: j['status'] as String? ?? '',
      turns: (j['turns'] as num?)?.toInt(),
      startedAt: _dtOf(j['started_at']),
      phase: j['phase'] as String? ?? '',
    );

Json traceWindow(TraceWindow w) =>
    {'turns': [for (final t in w.turns) turn(t)], 'start': w.start, 'end': w.end, 'file_length': w.fileLength, 'unchanged': w.unchanged,
     if (w.wire != null) 'wire': {'bytes': w.wire!.bytes, 'at': _dt(w.wire!.at), 'last_event': w.wire!.lastEvent}};
TraceWindow traceWindowOf(Json j) => TraceWindow(
      turns: [for (final t in _maps(j['turns'])) turnOf(t)],
      start: (j['start'] as num?)?.toInt() ?? 0,
      end: (j['end'] as num?)?.toInt() ?? 0,
      fileLength: (j['file_length'] as num?)?.toInt() ?? 0,
      unchanged: j['unchanged'] == true,
      wire: j['wire'] is Map
          ? Wire(bytes: ((j['wire'] as Map)['bytes'] as num?)?.toInt() ?? 0, at: _dtOf((j['wire'] as Map)['at']) ?? DateTime.now().toUtc(),
              lastEvent: (j['wire'] as Map)['last_event'] as String? ?? '')
          : null,
    );

// ---- procedures ------------------------------------------------------------

Json _origin(ProcedureOrigin o) =>
    {'task_id': o.taskId, 'task_title': o.taskTitle, 'work_order': o.workOrder, 'at': _dt(o.at)};
ProcedureOrigin _originOf(Json j) => ProcedureOrigin(
      taskId: j['task_id'] as String? ?? '',
      taskTitle: j['task_title'] as String? ?? '',
      workOrder: j['work_order'] as String? ?? '',
      at: _dtOf(j['at']),
    );

Json procedure(Procedure p) => {
      'id': p.id,
      'title': p.title,
      'description': p.description,
      'tags': p.tags,
      'steps': [
        for (final s in p.steps) {'id': s.id, 'title': s.title, 'do': s.do_, 'procedure': s.procedure},
      ],
      'updated_at': _dt(p.updatedAt),
      'minted_by': p.mintedBy == null ? null : _origin(p.mintedBy!),
      'used_by': [for (final o in p.usedBy) _origin(o)],
      'standing': {
        'retired': p.standing.retired,
        'pinned': p.standing.pinned,
        'protected': p.standing.protected,
        'grace_left': p.standing.graceLeft,
        'retired_at': p.standing.retiredAt,
        'retired_reason': p.standing.retiredReason,
        'touched': p.standing.touched,
      },
    };
Procedure procedureOf(Json j) {
  final st = (j['standing'] as Map?)?.cast<String, dynamic>() ?? const {};
  return Procedure(
    id: j['id'] as String,
    title: j['title'] as String? ?? '',
    description: j['description'] as String? ?? '',
    tags: _strs(j['tags']),
    steps: [
      for (final s in _maps(j['steps']))
        ProcedureStep(id: s['id'] as String? ?? '', title: s['title'] as String? ?? '', do_: s['do'] as String? ?? '', procedure: s['procedure'] as String?),
    ],
    updatedAt: _dtOf(j['updated_at']) ?? DateTime.fromMillisecondsSinceEpoch(0),
    mintedBy: j['minted_by'] == null ? null : _originOf((j['minted_by'] as Map).cast()),
    usedBy: [for (final o in _maps(j['used_by'])) _originOf(o)],
    standing: LibraryStanding(
      retired: st['retired'] == true,
      pinned: st['pinned'] == true,
      protected: st['protected'] == true,
      graceLeft: (st['grace_left'] as num?)?.toInt(),
      retiredAt: (st['retired_at'] as num?)?.toInt(),
      retiredReason: st['retired_reason'] as String?,
      touched: ((st['touched'] as Map?)?.cast<String, dynamic>() ?? const {}).map((k, v) => MapEntry(k, (v as num).toInt())),
    ),
  );
}

Json libraryPolicy(LibraryPolicy p) => {'enabled': p.enabled, 'grace': p.grace, 'clock': p.clock, 'weights': p.weights};
LibraryPolicy libraryPolicyOf(Json j) => LibraryPolicy(
      enabled: j['enabled'] == true,
      grace: (j['grace'] as num?)?.toInt() ?? 50,
      clock: (j['clock'] as num?)?.toInt() ?? 0,
      weights: ((j['weights'] as Map?)?.cast<String, dynamic>() ?? const {'edit': 3, 'use': 2, 'search': 1})
          .map((k, v) => MapEntry(k, (v as num).toInt())),
    );

Json procedureVersion(ProcedureVersion v) => {'sha': v.sha, 'at': _dt(v.at), 'author': v.author, 'message': v.message};
ProcedureVersion procedureVersionOf(Json j) => ProcedureVersion(
      sha: j['sha'] as String,
      at: _dtOf(j['at']) ?? DateTime.fromMillisecondsSinceEpoch(0),
      author: j['author'] as String? ?? '',
      message: j['message'] as String? ?? '',
    );

Json diffLine(DiffHunkLine l) => {'kind': l.kind, 'text': l.text};
DiffHunkLine diffLineOf(Json j) => DiffHunkLine(j['kind'] as String? ?? 'context', j['text'] as String? ?? '');

// ---- data book -------------------------------------------------------------

Json knownReport(KnownReport k) => {
      'id': k.id,
      'claim': k.claim,
      'value': k.value,
      'unit': k.unit,
      'type': k.type,
      'confidence': k.confidence,
      'n': k.n,
      'agreement': k.agreement,
      'methods': k.methods,
      'probes': k.probes,
      'runs': k.runs,
      'adopted_from': k.adoptedFrom,
      'at': _dt(k.at),
    };
KnownReport knownReportOf(Json j) => KnownReport(
      id: j['id'] as String? ?? '',
      claim: j['claim'] as String? ?? '',
      value: j['value'] as String? ?? '',
      unit: j['unit'] as String? ?? '',
      type: j['type'] as String? ?? '',
      confidence: j['confidence'] as String? ?? '',
      n: (j['n'] as num?)?.toInt() ?? 0,
      agreement: (j['agreement'] as num?)?.toDouble(),
      methods: (j['methods'] as num?)?.toInt() ?? 0,
      probes: _strs(j['probes']),
      runs: _strs(j['runs']),
      adoptedFrom: j['adopted_from'] as String?,
      at: _dtOf(j['at']),
    );

Json _openUnknown(OpenUnknown u) => {'id': u.id, 'claim': u.claim, 'evidence_needed': u.evidenceNeeded, 'status': u.status};
OpenUnknown _openUnknownOf(Json j) => OpenUnknown(
      id: j['id'] as String? ?? '',
      claim: j['claim'] as String? ?? '',
      evidenceNeeded: j['evidence_needed'] as String? ?? '',
      status: j['status'] as String? ?? '',
    );

Json dataBook(DataBook d) => {
      'entries': [
        for (final e in d.entries)
          {
            'kind': e.kind,
            'index': e.index,
            'text': e.text,
            'answers': [for (final a in e.answers) knownReport(a)],
            'open': [for (final u in e.open) _openUnknown(u)],
          },
      ],
      'orphans': [for (final k in d.orphans) knownReport(k)],
      'maps': [
        for (final m in d.maps)
          {
            'id': m.id,
            'kind': m.kind,
            'parent': m.parent,
            'purpose': m.purpose,
            'knowns': [
              for (final k in m.knowns) {'report': knownReport(k.report), 'adopted': k.adopted, 'from': k.from},
            ],
            'runs': m.runs,
            'open_unknowns': m.openUnknowns,
          },
      ],
    };
DataBook dataBookOf(Json j) => DataBook(
      entries: [
        for (final e in _maps(j['entries']))
          DataBookEntry(
            kind: e['kind'] as String? ?? '',
            index: (e['index'] as num?)?.toInt() ?? 0,
            text: e['text'] as String? ?? '',
            answers: [for (final a in _maps(e['answers'])) knownReportOf(a)],
            open: [for (final u in _maps(e['open'])) _openUnknownOf(u)],
          ),
      ],
      orphans: [for (final k in _maps(j['orphans'])) knownReportOf(k)],
      maps: [
        for (final m in _maps(j['maps']))
          MapNode(
            id: m['id'] as String? ?? '',
            kind: m['kind'] as String? ?? '',
            parent: m['parent'] as String?,
            purpose: m['purpose'] as String? ?? '',
            knowns: [
              for (final k in _maps(m['knowns']))
                MapKnown(report: knownReportOf((k['report'] as Map).cast()), adopted: k['adopted'] == true, from: k['from'] as String?),
            ],
            runs: (m['runs'] as num?)?.toInt() ?? 0,
            openUnknowns: (m['open_unknowns'] as num?)?.toInt() ?? 0,
          ),
      ],
    );

// ---- budget ------------------------------------------------------------------

Json _budgetLine(BudgetLine l) => {
      'id': l.id,
      'title': l.title,
      'status': l.status,
      'bucket': l.bucket,
      'points': l.points,
      'plan_points': l.planPoints,
      'sector': l.sector,
      'phase': l.phase,
      'priority': l.priority,
    };
BudgetLine _budgetLineOf(Json j) => BudgetLine(
      id: j['id'] as String? ?? '',
      title: j['title'] as String? ?? '',
      status: j['status'] as String? ?? '',
      bucket: j['bucket'] as String? ?? '',
      points: (j['points'] as num?)?.toInt() ?? 0,
      planPoints: (j['plan_points'] as num?)?.toInt(),
      sector: j['sector'] as String?,
      phase: j['phase'] as String? ?? '',
      priority: j['priority'] as String? ?? '',
    );
Json _sector(SectorLedger s) =>
    {'id': s.id, 'title': s.title, 'reserved': s.reserved, 'lines': [for (final l in s.lines) _budgetLine(l)]};
SectorLedger _sectorOf(Json j) => SectorLedger(
      id: j['id'] as String? ?? '',
      title: j['title'] as String? ?? '',
      reserved: (j['reserved'] as num?)?.toInt(),
      lines: [for (final l in _maps(j['lines'])) _budgetLineOf(l)],
    );
Json budgetLedger(BudgetLedger b) => {
      'budget': b.budget,
      'notes': b.notes,
      'locked': b.locked,
      'sectors': [for (final s in b.sectors) _sector(s)],
      'free_pool': _sector(b.freePool),
    };
BudgetLedger budgetLedgerOf(Json j) => BudgetLedger(
      budget: (j['budget'] as num?)?.toInt(),
      notes: j['notes'] as String? ?? '',
      locked: j['locked'] == true,
      sectors: [for (final s in _maps(j['sectors'])) _sectorOf(s)],
      freePool: _sectorOf((j['free_pool'] as Map?)?.cast<String, dynamic>() ?? const {}),
    );

// ---- providers ---------------------------------------------------------------

Json providerStatus(ProviderStatus s) => {
      'provider': s.name,
      'display_name': s.displayName,
      'auth_kind': s.authKind,
      'signed_in': s.signedIn,
      'quarantined': s.quarantined,
      'quarantine_reason': s.quarantineReason,
      'expired': s.expired,
      'updated_at': s.updatedAt,
      'metadata': s.account,
      'blocked': s.blocked,
      'reachable': s.reachable,
      'reason': s.reason,
      'api_base_url': s.baseUrl,
      'custom': s.custom,
      'endpoint': s.endpoint,
      'notes': s.notes,
    };
ProviderStatus providerStatusOf(Json j) => ProviderStatus.fromJson(j);

Json providerModels(ProviderModels m) => {
      'provider': m.provider,
      'models': m.models,
      'default_model': m.defaultModel,
      'context_window': m.contextWindow,
      'wire': m.wire,
      'source': m.source,
      'details': [
        for (final d in m.details.values)
          {'id': d.id, 'efforts': d.efforts, 'default_effort': d.defaultEffort, 'context_window': d.contextWindow},
      ],
    };
ProviderModels providerModelsOf(Json j) => ProviderModels.fromJson(j);

Json loginEvent(LoginEvent e) => switch (e) {
      LoginPrompt p => {
          'event': 'prompt',
          'kind': p.kind,
          'url': p.url,
          'user_code': p.userCode,
          'browser_opened': p.browserOpened,
          'browser_note': p.browserNote,
        },
      LoginDone d => {'event': 'done', 'status': providerStatus(d.status)},
      LoginFailed f => {'event': 'failed', 'error': f.error},
    };
LoginEvent loginEventOf(Json j) => switch (j['event']) {
      'prompt' => LoginPrompt(
          kind: j['kind'] as String? ?? 'browser',
          url: j['url'] as String? ?? '',
          userCode: j['user_code'] as String?,
          browserOpened: j['browser_opened'] == true,
          browserNote: j['browser_note'] as String? ?? '',
        ),
      'done' => LoginDone(providerStatusOf((j['status'] as Map).cast())),
      _ => LoginFailed(j['error'] as String? ?? 'unknown login event'),
    };

// ---- storage -----------------------------------------------------------------

Json storageReport(StorageReport r) => r.toJson();
StorageReport storageReportOf(Json j) => StorageReport.fromJson(j);
