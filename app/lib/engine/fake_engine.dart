import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import '../models/budget.dart';
import '../models/databook.dart';
import '../models/document.dart';

import '../models/loop.dart';
import 'engine.dart';
import 'fake_loop.dart';
import 'fake_route.dart';
import 'run_budget.dart';
import 'run_databook.dart';
import 'run_discovery.dart';
import 'run_inbox.dart';
import '../models/route.dart';

/// In-memory engine so the GUI can be built before the real one exists.
/// Seeded briefs follow Terra's brief.json shape
/// (engine/terra/src/terra/brief.py::default_brief).
class FakeEngine implements Engine {
  /// [runsRoot] is scanned for sessions alongside the engine's registry;
  /// the result is re-checked every [poll] and [changes] fires on a
  /// difference.
  FakeEngine({Directory? runsRoot, Duration poll = const Duration(seconds: 5)})
    : _discovery = runsRoot == null ? null : RunDiscovery(runsRoot) {
    rescan();
    setPoll(poll);
  }

  RunDiscovery? _discovery;
  Timer? _timer;

  /// Point discovery somewhere else; rescan at once.
  void setRunsRoot(Directory root) {
    _discovery = RunDiscovery(root);
    rescan();
  }

  void setPoll(Duration poll) {
    _timer?.cancel();
    _timer = _discovery == null ? null : Timer.periodic(poll, (_) => rescan());
  }
  final _changes = StreamController<void>.broadcast();
  String _signature = '';

  @override
  Stream<void> get changes => _changes.stream;

  final _runs = <String, RunProject>{};

  /// Re-discover runs and re-read their state; fire [changes] if anything
  /// about the project list or a run's files moved.
  void rescan() {
    final d = _discovery;
    if (d == null) return;
    final sig = StringBuffer();
    final seen = <String>{};
    for (final r in d.scan()) {
      final bf = File('${r.project}/.terra/brief.json');
      if (!bf.existsSync()) continue;
      seen.add(r.id);
      final rp = RunProject(
        id: r.id,
        project: r.project,
        session: r.session,
        running: r.running,
      );
      final was = _runs[r.id];
      _runs[r.id] = rp;
      _paths[r.id] = r.project;
      // The route on disk is the task table; the screen derives status
      // from it the way route.py does.
      final routeFile = File('${r.project}/.terra/route.json');
      if (routeFile.existsSync()) {
        final rs = routeFile.statSync();
        final rkey = '${rs.modified.millisecondsSinceEpoch}:${rs.size}';
        if (was == null || _routeStamp[r.id] != rkey) {
          try {
            final rj = jsonDecode(routeFile.readAsStringSync()) as Map<String, dynamic>;
            _routes[r.id] = FakeRoute([
              for (final t in (rj['tasks'] as List? ?? const []))
                (t as Map).cast<String, dynamic>(),
            ]);
            _routeStamp[r.id] = rkey;
          } catch (_) {}
        }
      }
      // The brief on disk is the truth for a run project; re-read when it
      // moved (the engine accepted a proposal, bumped a version).
      final bs = bf.statSync();
      final key = '${bs.modified.millisecondsSinceEpoch}:${bs.size}';
      if (was == null || _briefStamp[r.id] != key) {
        try {
          _briefs[r.id] = jsonDecode(bf.readAsStringSync()) as Map<String, dynamic>;
          _briefStamp[r.id] = key;
        } catch (_) {}
      }
      sig.write('${r.id}|${r.running}|$key|');
      for (final f in [
        File('${r.session}/loop.json'),
        File('${r.session}/controller.jsonl'),
        File('${r.session}/run.json'),
        File('${r.project}/.terra/route.json'),
      ]) {
        if (f.existsSync()) {
          final st = f.statSync();
          sig.write('${st.modified.millisecondsSinceEpoch}:${st.size};');
        }
      }
      final tasks = Directory('${r.session}/tasks');
      if (tasks.existsSync()) {
        for (final d in tasks.listSync()) {
          final rf = File('${d.path}/result.json');
          if (rf.existsSync()) sig.write('${rf.statSync().modified.millisecondsSinceEpoch};');
        }
      }
    }
    for (final id in _runs.keys.toList()) {
      if (!seen.contains(id)) {
        _runs.remove(id);
        _briefs.remove(id);
        _paths.remove(id);
        _routes.remove(id);
      }
    }
    final s = sig.toString();
    if (s != _signature) {
      _signature = s;
      _changes.add(null);
    }
  }

  final _briefStamp = <String, String>{};
  final _routeStamp = <String, String>{};

  void dispose() {
    _timer?.cancel();
    _changes.close();
  }

  @override
  Future<List<AttentionItem>> readAttention() async {
    final out = <AttentionItem>[];
    for (final id in _runs.keys.toList()) {
      final brief = _briefs[id];
      if (brief == null) continue;
      final summary = _summary(id, brief);
      final docs = await readInbox(id);
      for (final d in docs) {
        final wants = d.awaitingSignature ||
            d.kind == DocKind.anomaly ||
            (d.kind == DocKind.workOrder && d.hot) ||
            (d.kind == DocKind.closing && d.hot);
        if (wants) out.add(AttentionItem(project: summary, document: d));
      }
    }
    out.sort((a, b) {
      // Signatures first, then by time, newest first.
      if (a.document.awaitingSignature != b.document.awaitingSignature) {
        return a.document.awaitingSignature ? -1 : 1;
      }
      final at = a.document.at, bt = b.document.at;
      if (at == null || bt == null) return at == null ? 1 : -1;
      return bt.compareTo(at);
    });
    return out;
  }

  @override
  Future<List<InboxDocument>> readWorkOrders(String id) async {
    final r = _runs[id];
    if (r == null) return const [];
    return Isolate.run(
      () => RunInbox(project: Directory(r.project), session: Directory(r.session)).workOrders(),
    );
  }

  @override
  Future<BudgetLedger> readBudget(String id) async {
    final brief = _briefs[id];
    if (brief == null) return BudgetLedger.empty;
    final r = _runs[id];
    if (r != null) {
      final f = File('${r.project}/.terra/route.json');
      if (!f.existsSync()) return buildLedger(brief, const {});
      return Isolate.run(() {
        final route = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
        return buildLedger(brief, route);
      });
    }
    return buildLedger(brief, _status(id));
  }

  @override
  Future<DataBook> readDataBook(String id) async {
    final r = _runs[id];
    if (r == null) return DataBook.empty;
    return Isolate.run(() => RunDataBook(Directory(r.project)).read());
  }

  @override
  Future<List<InboxDocument>> readInbox(String id) async {
    final r = _runs[id];
    if (r == null) return const [];
    // Event logs are hundreds of megabytes; read off the UI isolate.
    return Isolate.run(
      () => RunInbox(
        project: Directory(r.project),
        session: Directory(r.session),
      ).read(),
    );
  }

  final Map<String, Map<String, dynamic>> _briefs = {
    'mizpah': _brief(
      title: 'Mizpah',
      mission: 'A self-improving engineering loop over Terra, Cartograph and Playbook.',
      needs: ['Controller mints unknowns and route tasks from brief vs map'],
      deliverables: ['Flutter desktop app that holds the brief'],
      enablers: [
        {
          'id': 'sidecar',
          'title': 'Engine sidecar protocol',
          'status': 'needed',
        },
        {
          'id': 'inline_edit',
          'title': 'Click-to-edit text line',
          'status': 'graduated',
          'path': 'app/lib/widgets/inline_text.dart',
          'graduates_to': 'frontend-inline-editable-text-flutter',
        },
      ],
      phases: [
        {
          'id': 'shell',
          'title': 'Shell',
          'description':
              'App shell, brief editor and proposal queue on a fake engine.',
          'status': 'open',
        },
        {
          'id': 'loop',
          'title': 'Loop',
          'description':
              'Controller and workers driving the route from the brief.',
          'status': 'open',
        },
      ],
      budgetPoints: 89,
      proposals: [
        {
          'id': 'CR-002',
          'summary':
              'Workers need a sandbox before any probe can run; '
              'this is an enabler, not a deliverable.',
          'status': 'open',
          'created_at': '2026-09-17T14:20:01Z',
          'patch': {
            'add_enabler': {
              'id': 'sandbox',
              'title': 'Sandboxed shell for worker probes',
              'status': 'needed',
            },
          },
        },
        {
          'id': 'CR-003',
          'summary':
              'Route tasks keep drifting into building an MCP '
              'server. The README says the harness couples tools directly.',
          'status': 'open',
          'created_at': '2026-09-17T15:20:00Z',
          'patch': {'add_non_goal': 'MCP servers or plugin manifests'},
        },
        {
          'id': 'CR-001',
          'summary': 'Initial budget felt low for three forked tools.',
          'status': 'rejected',
          'created_at': '2026-09-17T11:00:00Z',
          'patch': {'note': 'Raise budget_points to 144'},
        },
      ],
    ),
    'scratch': _brief(
      title: 'Scratch project',
      mission: 'Throwaway brief for trying the editor.',
    ),
  };

  final _routes = <String, FakeRoute>{
    'mizpah': FakeRoute.mizpah(),
    'scratch': FakeRoute([]),
  };

  Map<String, dynamic> _status(String id) {
    final b = _briefs[id]!;
    return (_routes[id] ??= FakeRoute([])).status(
      (b['phases'] as List).cast<Map<String, dynamic>>(),
      b['budget_points'] as int?,
    );
  }

  @override
  Future<RouteStatus> readRoute(String id) async =>
      RouteStatus.fromJson(_status(id));

  @override
  Future<List<RouteEvent>> readRouteLog(String id) async => [
    for (final e in (_routes[id] ??= FakeRoute([])).log) RouteEvent.fromJson(e),
  ];

  @override
  Future<void> setPriority(
    String id,
    String taskId,
    String priority,
    String reason,
  ) async => _routes[id]!.setPriority(taskId, priority, reason);

  @override
  Future<void> cancelTask(String id, String taskId, String reason) async =>
      _routes[id]!.cancel(taskId, reason);

  @override
  Future<void> unblockTask(String id, String taskId) async =>
      _routes[id]!.unblock(taskId);

  /// What `route cancel` would strand — the app asks before cancelling.
  List<String> strandedBy(String id, String taskId) =>
      _routes[id]!.strandedBy(taskId);

  final _loops = <String, FakeLoop>{};

  @override
  Stream<LoopState> watchLoop(String id) => (_loops[id] ??= FakeLoop()).stream;

  /// The engine's loop module, run from the same venv as `terra`.
  String get _pythonExecutable =>
      terraExecutable.replaceFirst(RegExp(r'/terra$'), '/python');

  /// A run project's dial: `run` relaunches the loop on the same session
  /// root when none is alive (a stopped run resumes: its tasks, its
  /// accepted proposals); `hold` leaves a STOP file the loop reads at its
  /// next boundary. `propose` is not an engine mode yet and is treated as
  /// hold. Seeded demo briefs keep the in-memory loop.
  @override
  Future<void> setMode(String id, LoopMode mode) async {
    final run = _runs[id];
    if (run == null) {
      (_loops[id] ??= FakeLoop()).setMode(mode);
      return;
    }
    final stop = File('${run.session}/STOP');
    if (mode != LoopMode.run) {
      stop.writeAsStringSync('hold from the app ${DateTime.now().toIso8601String()}\n');
      _changes.add(null);
      return;
    }
    if (stop.existsSync()) stop.deleteSync();
    if (run.running) return;
    final rf = File('${run.session}/run.json');
    final rj = rf.existsSync()
        ? jsonDecode(rf.readAsStringSync()) as Map<String, dynamic>
        : const <String, dynamic>{};
    final config = rj['mizpah_config'] as String?;
    if (config == null || config.isEmpty) {
      throw StateError(
        'run.json for ${run.id} names no mizpah_config; the loop that made it predates resume',
      );
    }
    final engineDir = File(config).parent.path;
    await Process.start(_pythonExecutable, [
      '-m',
      'mizpah.loop',
      '--config',
      config,
      '--project',
      run.project,
      '--root',
      run.session,
      '--max-cycles',
      '40',
      '--max-tasks',
      '60',
      '--deadline-hours',
      '4',
    ], workingDirectory: engineDir, mode: ProcessStartMode.detached);
    _changes.add(null);
  }

  @override
  Stream<List<Turn>> watchTranscript(String id, String session) =>
      (_loops[id] ??= FakeLoop()).transcript(session);

  final _paths = <String, String>{
    'mizpah': '/home/user/mizpah',
    'scratch': '/home/user/scratch',
  };

  @override
  Future<List<BriefSummary>> listBriefs() async => [
    for (final e in _briefs.entries) _summary(e.key, e.value),
  ];

  /// A run's state is its loop's: live while the process is up, completed
  /// on nothing_owed, stopped for any other end. A stall or an open change
  /// request counts as attention wherever the project sits.
  BriefSummary _summary(String id, Map<String, dynamic> brief) {
    final r = _runs[id];
    var state = 'idle';
    var attention = (brief['proposals'] as List? ?? const [])
        .where((p) => (p as Map)['status'] == 'open')
        .length;
    if (r != null) {
      String? stop;
      final f = File('${r.session}/loop.json');
      if (f.existsSync()) {
        try {
          final loop = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
          stop = loop['stop'] as String?;
        } catch (_) {}
      }
      if (stop == 'controller_stalled') attention++;
      state = r.running
          ? 'live'
          : stop == 'nothing_owed'
          ? 'completed'
          : 'stopped'; // any other reason, or a loop killed before it wrote one
    }
    return BriefSummary(
      id: id,
      title: brief['title'] as String? ?? id,
      path: _paths[id] ?? '',
      state: state,
      attention: attention,
    );
  }

  @override
  Future<Map<String, dynamic>> readBrief(String id) async {
    final b = _briefs[id];
    if (b == null) throw ArgumentError.value(id, 'id', 'no such brief');
    return b;
  }

  @override
  Future<void> writeBrief(String id, Map<String, dynamic> brief) async {
    final current = _briefs[id];
    if (current == null) throw ArgumentError.value(id, 'id', 'no such brief');
    // Terra bumps version on every direct write (brief set, add need, …).
    final stored = Map<String, dynamic>.of(brief);
    if (_canonical(brief) != _canonical(current)) {
      stored['version'] = (current['version'] as int? ?? 1) + 1;
    }
    _briefs[id] = stored;
  }

  static Map<String, dynamic> _brief({
    required String title,
    required String mission,
    List<String> needs = const [],
    List<String> nonGoals = const [],
    List<String> deliverables = const [],
    List<Map<String, String>> enablers = const [],
    List<Map<String, String>> phases = const [],
    int? budgetPoints,
    List<Map<String, Object>> proposals = const [],
  }) => {
    'schema_version': 1,
    'id': 'brief',
    'title': title,
    'version': 1,
    'status': 'draft',
    'mission': mission,
    'budget_points': budgetPoints,
    'budget_notes': '',
    'needs': needs,
    'non_goals': nonGoals,
    'deliverables': deliverables,
    'enablers': enablers,
    'phases': phases,
    'proposals': proposals,
  };

  /// JSON with keys sorted at every level, so order never reads as a change.
  static String _canonical(Object? v) => jsonEncode(_sorted(v));
  static Object? _sorted(Object? v) => switch (v) {
    Map m => {
      for (final k in m.keys.map((k) => '$k').toList()..sort())
        k: _sorted(m[k]),
    },
    List l => [for (final e in l) _sorted(e)],
    _ => v,
  };

  Map<String, dynamic> _proposal(String id, String proposalId) {
    final b = _briefs[id];
    if (b == null) throw ArgumentError.value(id, 'id', 'no such brief');
    final props = (b['proposals'] as List).cast<Map<String, dynamic>>();
    final p = props.firstWhere(
      (p) => p['id'] == proposalId,
      orElse: () => throw ArgumentError.value(proposalId, 'proposalId'),
    );
    if (p['status'] != 'open') {
      throw StateError('proposal $proposalId is ${p['status']}');
    }
    return p;
  }

  /// Path to the `terra` script beside `mizpah-provider` in the engine's
  /// venv; a run project's proposals are decided through it so the brief
  /// on disk moves and a live loop sees the decision at its next step.
  String terraExecutable = '/home/Vinscen/mizpah/.venv/bin/terra';

  Future<void> _terraBrief(RunProject r, List<String> args) async {
    final res = await Process.run(terraExecutable, [
      'brief',
      ...args,
    ], workingDirectory: r.project);
    if (res.exitCode != 0) {
      throw StateError(
        'terra brief ${args.join(' ')} failed: ${(res.stderr as String).trim()} ${(res.stdout as String).trim()}',
      );
    }
    final bf = File('${r.project}/.terra/brief.json');
    _briefs[r.id] = jsonDecode(bf.readAsStringSync()) as Map<String, dynamic>;
    _briefStamp.remove(r.id);
    _changes.add(null);
  }

  /// A run project: `terra brief accept` in its directory (the brief on
  /// disk changes; a running loop picks it up at its next step). A seeded
  /// demo brief: mirrors terra.brief.accept_proposal in memory.
  @override
  Future<void> acceptProposal(String id, String proposalId) async {
    final run = _runs[id];
    if (run != null) {
      _proposal(id, proposalId); // refuses an already-decided proposal
      await _terraBrief(run, ['accept', proposalId]);
      return;
    }
    final b = _briefs[id]!;
    final p = _proposal(id, proposalId);
    final patch = (p['patch'] as Map).cast<String, dynamic>();
    void push(String key, Object v) =>
        b[key] = [...(b[key] as List? ?? const []), v];
    const lists = {
      'add_need': 'needs',
      'add_non_goal': 'non_goals',
      'add_deliverable': 'deliverables',
      'add_enabler': 'enablers',
    };
    for (final e in lists.entries) {
      if (patch[e.key] != null) push(e.value, patch[e.key]!);
    }
    if (patch['mission'] != null) b['mission'] = patch['mission'];
    p['status'] = 'accepted';
    b['version'] = (b['version'] as int? ?? 1) + 1;
  }

  @override
  Future<void> rejectProposal(String id, String proposalId) async {
    final run = _runs[id];
    if (run != null) {
      _proposal(id, proposalId);
      await _terraBrief(run, ['reject', proposalId]);
      return;
    }
    _proposal(id, proposalId)['status'] = 'rejected';
  }
}

/// A run on disk, registered as a project: the Terra project root and the
/// session root the loop wrote beside it.
class RunProject {
  const RunProject({
    required this.id,
    required this.project,
    required this.session,
    this.running = false,
  });
  final String id;
  final String project;
  final String session;
  final bool running;
}
