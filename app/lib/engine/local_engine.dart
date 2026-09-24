import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import '../models/budget.dart';
import '../models/databook.dart';
import '../models/environment.dart';
import '../models/document.dart';
import '../models/procedure.dart';

import '../models/loop.dart';
import 'deputy_activity.dart';
import 'engine.dart';
import 'engine_home.dart';
import 'fake_loop.dart';
import 'fake_route.dart';
import 'run_budget.dart';
import 'run_databook.dart';
import '../cg/app_paths/app_paths.dart';
import 'platform_paths.dart';
import 'procedure_history.dart';
import 'procedure_store.dart';
import 'run_discovery.dart';
import 'run_floor.dart';
import 'run_inbox.dart';
import 'run_tool.dart';
import 'storage_scan.dart' as scan;
import '../models/storage.dart';
import 'environments_read.dart';
import 'environments_read.dart' as env_fs;
import 'desk_notices.dart';
import 'file_settings_store.dart';
import '../models/route.dart';
import 'state_dir.dart';

/// The engine on the machine that holds the files: it discovers projects
/// and runs, reads their paper, launches loops, signs briefs and talks to
/// the Deputy by spawning the Python engine's tools. [RemoteEngine] is the
/// same interface over RPC for a desk elsewhere. (Born as FakeEngine, an
/// in-memory stand-in for building the GUI; the name outlived the fake.)
class LocalEngine implements Engine {
  /// [runsRoot] is scanned for sessions alongside the engine's registry;
  /// the result is re-checked every [poll] and [changes] fires on a
  /// difference.
  LocalEngine({Directory? runsRoot, Duration poll = const Duration(seconds: 5), File? marksFile, Directory? boardDir})
    : _discovery = runsRoot == null ? null : RunDiscovery(runsRoot),
      // ignore: prefer_initializing_formals
      _marksFile = marksFile,
      // ignore: prefer_initializing_formals
      _boardDir = boardDir {
    rescan();
    setPoll(poll);
  }

  /// Where the read/dismissed marks live; a test passes its own so it
  /// never writes over the person's (the roundtrip test did, every run).
  final File? _marksFile;

  /// The Board's notice files; a test passes its own, else `notices/board`
  /// under the engine home.
  final Directory? _boardDir;

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

  /// Who signs from this seat, as it is written on the paper ("Name,
  /// Title"). Terra stores it on the proposal or brief at the moment of
  /// decision; the app never renders a signature from settings afterwards.
  String signer = '';

  /// The mark as PNG bytes, if the person has one (drawn or uploaded).
  /// Called at signing time so the mark filed is the one current then.
  Future<List<int>?> Function()? signatureMark;

  /// FNV-1a over the bytes, as 16 hex digits: a stable name for one mark.
  static String _fnv(List<int> bytes) {
    var h = 0xcbf29ce484222325;
    for (final b in bytes) {
      h = ((h ^ b) * 0x100000001b3) & 0x7FFFFFFFFFFFFFFF;
    }
    return h.toRadixString(16).padLeft(16, '0');
  }

  /// `--signed-by` and, when there is a mark, `--signature` pointing at a
  /// copy filed under the task's state dir. Content-addressed: signing ten
  /// documents with one hand files one file.
  Future<List<String>> _signedBy(String project) async {
    if (signer.trim().isEmpty) return const [];
    final args = ['--signed-by', signer.trim()];
    final bytes = await signatureMark?.call();
    if (bytes == null || bytes.isEmpty) return args;
    final rel = 'signatures/${_fnv(bytes)}.png';
    final f = File('${stateDir(project)}/$rel');
    if (!f.existsSync()) {
      f.parent.createSync(recursive: true);
      f.writeAsBytesSync(bytes);
    }
    return [...args, '--signature', rel];
  }

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
    // A project has one row here and as many sessions as it has run.
    // Discovery yields one row per session; group them, newest first.
    final byProject = <String, List<DiscoveredRun>>{};
    for (final r in d.scan()) {
      if (!File('${stateDir(r.project)}/brief.json').existsSync()) continue;
      byProject.putIfAbsent(r.id, () => []).add(r);
    }
    for (final entry in byProject.entries) {
      final rows = entry.value..sort(_newestFirst);
      _sessions[entry.key] = [
        for (final r in rows)
          if (r.session.isNotEmpty)
            RunSession(path: r.session, startedAt: r.startedAt, running: r.running, archived: r.archived, stop: r.stop),
      ];
      final r = _pick(entry.key, rows);
      final bf = File('${stateDir(r.project)}/brief.json');
      seen.add(r.id);
      // The row is live while any session is, whichever one is shown: a
      // decision must not relaunch a loop beside the one running.
      final live = rows.any((x) => x.running);
      final rp = RunProject(
        id: r.id,
        project: r.project,
        session: r.session,
        running: live,
        archived: r.archived && !live,
      );
      final was = _runs[r.id];
      _runs[r.id] = rp;
      _paths[r.id] = r.project;
      // The route on disk is the task table; the screen derives status
      // from it the way route.py does.
      final routeFile = File('${stateDir(r.project)}/route.json');
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
      sig.write('${r.id}|${r.session}|$live|${r.running}|${r.archived}|$key|');
      for (final f in [
        File('${r.session}/loop.json'),
        File('${r.session}/controller.jsonl'),
        File('${r.session}/run.json'),
        File('${stateDir(r.project)}/route.json'),
      ]) {
        if (f.existsSync()) {
          final st = f.statSync();
          sig.write('${st.modified.millisecondsSinceEpoch}:${st.size};');
        }
      }
      // Task results, and while a task runs its journal (every turn moves
      // it) and its outages: a work order's turns and an anomaly arrive
      // under the reader, not at the task's end.
      final tasks = Directory('${r.session}/tasks');
      if (tasks.existsSync()) {
        for (final d in tasks.listSync()) {
          final rf = File('${d.path}/result.json');
          if (rf.existsSync()) {
            sig.write('${rf.statSync().modified.millisecondsSinceEpoch};');
            continue;
          }
          for (final f in [File('${d.path}/events/session.jsonl'), File('${d.path}/outages.jsonl')]) {
            if (f.existsSync()) sig.write('${f.statSync().modified.millisecondsSinceEpoch};');
          }
        }
      }
      final outages = File('${r.session}/outages.jsonl');
      if (outages.existsSync()) sig.write('${outages.statSync().modified.millisecondsSinceEpoch};');
    }
    for (final id in _runs.keys.toList()) {
      if (!seen.contains(id)) {
        _runs.remove(id);
        _briefs.remove(id);
        _paths.remove(id);
        _routes.remove(id);
        _sessions.remove(id);
        _chosen.remove(id);
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

  /// Every session per project, newest first; and the one the person
  /// chose to look at, when they did.
  final _sessions = <String, List<RunSession>>{};
  final _chosen = <String, String>{};

  static int _newestFirst(DiscoveredRun a, DiscoveredRun b) {
    final ta = a.startedAt?.millisecondsSinceEpoch ?? 0;
    final tb = b.startedAt?.millisecondsSinceEpoch ?? 0;
    return tb.compareTo(ta);
  }

  /// Which session the project's paperwork comes from: the chosen one if
  /// it is still there; else the live one; else the newest that is not
  /// archived; else the newest. A draft (no session) is its own row.
  DiscoveredRun _pick(String id, List<DiscoveredRun> rows) {
    final chosen = _chosen[id];
    if (chosen != null) {
      final c = rows.where((r) => r.session == chosen).firstOrNull;
      if (c != null) return c;
      _chosen.remove(id);
    }
    return rows.where((r) => r.running).firstOrNull ??
        rows.where((r) => r.session.isNotEmpty && !r.archived).firstOrNull ??
        rows.first;
  }

  @override
  List<RunSession> sessions(String id) => _sessions[id] ?? const [];

  @override
  String? currentSession(String id) {
    final s = _runs[id]?.session;
    return s == null || s.isEmpty ? null : s;
  }

  @override
  void chooseSession(String id, String session) {
    if (_runs[id]?.session == session) return;
    _chosen[id] = session;
    rescan();
  }

  @override
  Future<void> archiveSession(String id, String session) async {
    final rf = File('$session/run.json');
    Map<String, dynamic> r = const {};
    try {
      if (rf.existsSync()) r = jsonDecode(rf.readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {}
    r = Map.of(r)
      ..['archived'] = true
      ..['archived_why'] = 'archived from the app ${DateTime.now().toIso8601String()}';
    rf.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(r));
    // The controller's library skips a record whose session is archived; nothing else to do.
    _chosen.remove(id);
    rescan();
  }

  /// Every session root of a project, from the scan (not just the shown one).
  List<RunSession> _allSessions(String id) => _sessions[id] ?? const [];

  Future<void> _setArchived(String session, bool archived) async {
    final rf = File('$session/run.json');
    Map<String, dynamic> r = const {};
    try {
      if (rf.existsSync()) r = jsonDecode(rf.readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {}
    r = Map.of(r);
    if (archived) {
      r['archived'] = true;
      r['archived_why'] = 'archived from the app ${DateTime.now().toIso8601String()}';
    } else {
      r.remove('archived');
      r.remove('archived_why');
    }
    rf.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(r));
  }

  @override
  Future<void> archiveProject(String id) async {
    final r = _runs[id];
    if (r == null) throw StateError('no project listed as $id');
    if (r.running) throw StateError('$id is live; hold it first');
    for (final s in _allSessions(id)) {
      if (!s.archived) await _setArchived(s.path, true);
    }
    _chosen.remove(id);
    rescan();
    _changes.add(null);
  }

  @override
  Future<void> unarchiveProject(String id) async {
    for (final s in _allSessions(id)) {
      if (s.archived) await _setArchived(s.path, false);
    }
    _chosen.remove(id);
    rescan();
    _changes.add(null);
  }

  @override
  Future<void> deleteProject(String id) async {
    final r = _runs[id];
    if (r == null) throw StateError('no project listed as $id');
    if (r.running) throw StateError('$id is live; hold it first');
    for (final s in _allSessions(id)) {
      await deleteSession(id, s.path);
    }
    final gyms = RunDiscovery.gymsRoot().path;
    if (r.project.startsWith('$gyms/')) {
      final folder = Directory(r.project);
      if (folder.existsSync()) folder.deleteSync(recursive: true);
      for (final side in ['${r.project}.out', '${r.project}.err']) {
        final f = File(side);
        if (f.existsSync()) f.deleteSync();
      }
    } else {
      final state = Directory(stateDir(r.project));
      if (state.existsSync()) state.deleteSync(recursive: true);
    }
    final pr = RunDiscovery.projectsRegistry();
    if (pr.existsSync()) {
      final kept = pr.readAsLinesSync().where((l) => !l.contains('"${r.project}"')).toList();
      pr.writeAsStringSync(kept.isEmpty ? '' : '${kept.join('\n')}\n');
    }
    _runs.remove(id);
    _briefs.remove(id);
    _sessions.remove(id);
    rescan();
    _changes.add(null);
  }

  @override
  Future<void> discardDraft(String id) async {
    final r = _runs[id];
    final brief = _briefs[id];
    if (r == null || brief == null) throw StateError('no project listed as $id');
    if (r.session.isNotEmpty) throw StateError('$id has run; delete its sessions first');
    final gyms = RunDiscovery.gymsRoot().path;
    if (brief['status'] != 'draft') {
      // An issued brief is never discarded through the Deputy — but a gym whose
      // every session was deleted is a remnant of a run, not a draft, and the
      // only thing left to do with it is remove the folder.
      if (!r.project.startsWith('$gyms/')) throw StateError('the brief of $id is issued; an issued brief is not discarded');
      final folder = Directory(r.project);
      if (folder.existsSync()) folder.deleteSync(recursive: true);
      for (final side in ['${r.project}.out', '${r.project}.err']) {
        final f = File(side);
        if (f.existsSync()) f.deleteSync();
      }
    } else if (r.project.startsWith('$gyms/')) {
      final name = r.project.substring(gyms.length + 1).split('/').first;
      final res = await runTool(_pythonExecutable, ['-m', 'mizpah.draft', 'discard', name], workingDirectory: engine.mizpahDir);
      if (res.exitCode != 0) {
        throw StateError(((res.stderr as String).trim().isEmpty ? res.stdout : res.stderr).toString().trim());
      }
    } else {
      final state = Directory(stateDir(r.project));
      if (state.existsSync()) state.deleteSync(recursive: true);
    }
    // The registry line, so the folder is not listed as a draft again.
    final pr = RunDiscovery.projectsRegistry();
    if (pr.existsSync()) {
      final kept = pr.readAsLinesSync().where((l) => !l.contains('"${r.project}"')).toList();
      pr.writeAsStringSync(kept.isEmpty ? '' : '${kept.join('\n')}\n');
    }
    _runs.remove(id);
    _briefs.remove(id);
    rescan();
    _changes.add(null);
  }

  @override
  Future<void> deleteSession(String id, String session) async {
    final dir = Directory(session);
    if (dir.existsSync()) dir.deleteSync(recursive: true);
    // The last session of a gym takes the gym with it: a project folder with
    // an issued brief and no session is neither a run nor a draft (the Deputy
    // never discards an issued brief), and it sat in Drafts with nothing that
    // could remove it. A project outside the gyms root is someone's tree and
    // keeps its folder.
    final project = _runs[id]?.project;
    if (project != null) {
      final sessions = Directory('${stateDir(project)}${pathSep}sessions');
      final left = sessions.existsSync() ? sessions.listSync().whereType<Directory>().length : 0;
      final gyms = RunDiscovery.gymsRoot().path;
      if (left == 0 && project.startsWith('$gyms/')) {
        final folder = Directory(project);
        if (folder.existsSync()) folder.deleteSync(recursive: true);
        for (final side in ['$project.out', '$project.err']) {
          final f = File(side);
          if (f.existsSync()) f.deleteSync();
        }
        final pr = RunDiscovery.projectsRegistry();
        if (pr.existsSync()) {
          final kept = pr.readAsLinesSync().where((l) => !l.contains('"$project"')).toList();
          pr.writeAsStringSync(kept.isEmpty ? '' : '${kept.join('\n')}\n');
        }
        _runs.remove(id);
        _briefs.remove(id);
      }
    }
    // Registry lines that name this session root, and the library record that carries it.
    final state = mizpahPaths().state;
    for (final name in ['runs.jsonl']) {
      final f = File('$state$pathSep$name');
      if (!f.existsSync()) continue;
      final kept = f.readAsLinesSync().where((l) => !l.contains('"$session"')).toList();
      f.writeAsStringSync(kept.isEmpty ? '' : '${kept.join('\n')}\n');
    }
    final briefs = Directory('${mizpahPaths().data}${pathSep}briefs');
    if (briefs.existsSync()) {
      for (final f in briefs.listSync()) {
        if (f is File && f.path.endsWith('.json')) {
          try {
            final doc = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
            if (doc['session'] == session) f.deleteSync();
          } catch (_) {}
        }
      }
    }
    _chosen.remove(id);
    rescan();
  }

  void dispose() {
    _timer?.cancel();
    _changes.close();
  }

  /// The engine config the app launches loops with (from settings).
  /// Set once from settings at launch; every tool path comes from here.
  EngineHome engine = const EngineHome('');
  String engineConfig = '';

  @override
  Future<void> startTask(String id) async {
    final r = _runs[id];
    if (r == null) throw StateError('no such task');
    if (r.running) return;
    if (r.session.isNotEmpty) return _resumeIfStopped(r);
    // First start. Terra keeps the brief as a draft until it is set active;
    // the loop routes only an active brief.
    final dirName = stateDirName(r.project);
    final env = {...Platform.environment, 'TERRA_DIRNAME': dirName};
    // The crew as pinned in the project config goes on the brief with the
    // signature (issued_crew): the paper says what it was signed to run on.
    // A task keeps the crew it first runs with: pin every seat it has not
    // chosen from the engine config now, so the brief is signed on it and a
    // later change of the defaults never swaps the model under the task.
    if (engineConfig.isNotEmpty) {
      await runTool(_pythonExecutable, ['-m', 'mizpah.init', 'pin', '--config', engineConfig, r.project],
          workingDirectory: engine.mizpahDir);
    }
    final crew = _pinnedCrew(r.project);
    final act = await runTool(terraExecutable, ['brief', 'set', '--status', 'active', ...await _signedBy(r.project),
        if (crew.isNotEmpty) ...['--crew', jsonEncode(crew)]],
        workingDirectory: r.project, environment: env);
    if (act.exitCode != 0) {
      throw StateError('terra brief set failed: ${(act.stderr as String).trim()} ${(act.stdout as String).trim()}');
    }
    final stamp = DateTime.now().toUtc().toIso8601String().replaceAll(RegExp(r'[-:]'), '').split('.').first;
    final session = Directory('${stateDir(r.project)}/sessions/$stamp')..createSync(recursive: true);
    final engineDir = engine.mizpahDir;
    await Process.start(_pythonExecutable, [
      '-m', 'mizpah.loop',
      '--config', engineConfig,
      '--project', r.project,
      '--root', session.path,
      // Points are the limiter, never wall time: no --deadline-hours (it
      // stopped two runs on 'deadline' with readings still owed). The cycle
      // and task caps are safety only.
      '--max-cycles', '40',
      '--max-tasks', '60',
    ], workingDirectory: engineDir, mode: ProcessStartMode.detached);
    // The loop writes run.json within a second; rescan a couple of times.
    for (var i = 0; i < 6; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 500));
      rescan();
      if (_runs[id]?.session.isNotEmpty ?? false) break;
    }
    _changes.add(null);
  }

  /// A new task is `mizpah init`: `.mizpah/` with a brief, route and map
  /// inside the person's repository, or a fresh gym when they have none.
  /// The engine registers the project; a rescan then lists it as a draft.
  @override
  Future<String> createTask(String title, {required String mission, String? repo}) async {
    final python = engine.python;
    final args = [
      '-m', 'mizpah.init',
      if (repo != null && repo.trim().isNotEmpty) repo.trim() else '--gym',
      '--title', title,
      '--mission', mission,
    ];
    final res = await runTool(python, args, workingDirectory: engine.mizpahDir);
    if (res.exitCode != 0) {
      throw StateError(((res.stderr as String).trim().isEmpty ? res.stdout : res.stderr).toString().trim());
    }
    String? project;
    try {
      project = (jsonDecode(res.stdout as String) as Map)['project'] as String?;
    } catch (_) {}
    rescan();
    if (project == null) return '';
    return _runs.keys.firstWhere((id) => _runs[id]!.project == project, orElse: () => '');
  }

  /// The Deputy: `python -m mizpah.deputy --config <engine> say <text>`.
  /// One process per turn; the engine keeps the session and the log.
  Directory get _deputyDir => Directory('${mizpahPaths().state}${pathSep}deputy');

  Future<Map<String, dynamic>> _deputy(List<String> args) async {
    final engineDir = engine.mizpahDir;
    // A model turn: minutes are normal. The engine bounds the turn itself (tool-call cap, model timeout,
    // the STOP file); this ceiling is only against a process that will never return.
    final res = await runTool(_pythonExecutable, ['-m', 'mizpah.deputy', '--config', engineConfig, ...args],
        workingDirectory: engineDir, timeout: const Duration(minutes: 30));
    final line = (res.stdout as String).trim().split('\n').where((l) => l.startsWith('{')).lastOrNull ?? '';
    Map<String, dynamic> j = const {};
    try {
      j = jsonDecode(line) as Map<String, dynamic>;
    } catch (_) {}
    if (res.exitCode != 0 || j['status'] != 'ok') {
      throw StateError((j['error'] as String?) ?? ((res.stderr as String).trim().isEmpty ? res.stdout : res.stderr).toString().trim());
    }
    return j;
  }

  @override
  Future<void> deputySay(String text) async {
    await _deputy(['say', text]);
    rescan(); // a draft may have been made or discarded
    _changes.add(null);
  }

  @override
  Future<List<DeputyStep>> readDeputySteps() {
    final path = '${_deputyDir.path}${pathSep}events${pathSep}session.jsonl';
    return Isolate.run(() => DeputyActivity(File(path)).current());
  }

  @override
  Future<Map<String, dynamic>?> readDeputyActivity() async {
    final f = File('${_deputyDir.path}${pathSep}activity.json');
    if (!f.existsSync()) return null;
    try {
      return (jsonDecode(await f.readAsString()) as Map).cast<String, dynamic>();
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> stopDeputy() async {
    await _deputy(['stop']);
  }

  @override
  Future<List<DeputyTurn>> readDeputyTurns() async {
    final f = File('${_deputyDir.path}${pathSep}turns.jsonl');
    if (!f.existsSync()) return const [];
    final out = <DeputyTurn>[];
    for (final line in await f.readAsLines()) {
      if (line.trim().isEmpty) continue;
      try {
        out.add(DeputyTurn.fromJson(jsonDecode(line) as Map<String, dynamic>));
      } catch (_) {}
    }
    return out;
  }

  @override
  Future<Map<String, dynamic>?> readDeputyShowing() async {
    final f = File('${_deputyDir.path}${pathSep}showing.json');
    if (!f.existsSync()) return null;
    try {
      return (jsonDecode(await f.readAsString()) as Map).cast<String, dynamic>();
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> resetDeputy() async {
    await _deputy(['reset']);
    _changes.add(null);
  }

  /// Signing a draft: `mizpah.draft authorize <project>` furnishes it where
  /// it is (a Deputy draft is a gym with no config or registry entry yet;
  /// for a project that is one already this changes nothing) or moves it
  /// into the repository named; the rescan then lists it under its id,
  /// which is returned.
  @override
  Future<String> authorizeDraft(String id, {String? repo}) async {
    final path = _runs[id]?.project;
    if (path == null) throw StateError('no project listed as $id');
    final engineDir = engine.mizpahDir;
    final res = await runTool(_pythonExecutable, [
      '-m', 'mizpah.draft', 'authorize', path,
      if (repo != null && repo.trim().isNotEmpty) ...['--repo', repo.trim()],
      // Locks the crew in: roles riding on your default get it copied into
      // the project, so the run uses what the brief is signed on.
      if (engineConfig.isNotEmpty) ...['--config', engineConfig],
    ], workingDirectory: engineDir);
    final line = (res.stdout as String).trim().split('\n').where((l) => l.startsWith('{')).lastOrNull ?? '';
    Map<String, dynamic> j = const {};
    try {
      j = jsonDecode(line) as Map<String, dynamic>;
    } catch (_) {}
    if (res.exitCode != 0 || j['status'] != 'ok') {
      throw StateError((j['error'] as String?) ?? ((res.stderr as String).trim().isEmpty ? res.stdout : res.stderr).toString().trim());
    }
    final project = j['project'] as String;
    rescan();
    final newId = _runs.keys.firstWhere((k) => _runs[k]!.project == project, orElse: () => '');
    if (newId.isEmpty) throw StateError('authorized $id into $project but the rescan did not list it');
    _changes.add(null);
    return newId;
  }

  /// `{role: {provider, model, effort}}` from the project's `models.<role>`
  /// specs, as `mizpah.init.crew_label` reads them.
  static Map<String, Map<String, dynamic>> _pinnedCrew(String project) {
    Map<String, dynamic>? pc;
    try {
      pc = jsonDecode(File('${stateDir(project)}/config.json').readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return const {};
    }
    final models = (pc['models'] as Map?)?.cast<String, dynamic>() ?? const {};
    return {
      for (final role in const ['worker', 'controller'])
        if (models[role] is Map)
          role: {
            'provider': (models[role] as Map)['subscription'] ?? (models[role] as Map)['provider'],
            'model': ((models[role] as Map)['generation'] as Map?)?['model'],
            'effort': ((models[role] as Map)['generation'] as Map?)?['reasoning_effort'],
          },
    };
  }

  static String _slug(String title) {
    final s = title.trim().toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-').replaceAll(RegExp(r'^-+|-+$'), '');
    return s.length > 48 ? s.substring(0, 48).replaceAll(RegExp(r'-+$'), '') : s;
  }

  /// The playbook's global store: the `playbook` data dir on this platform.
  String get playbookExecutable => engine.playbook;
  Directory get procedureDir => Directory(
        '${resolveAppPaths('playbook', environ: Platform.environment, platform: Platform.operatingSystem).data}${pathSep}procedures',
      );

  @override
  Future<List<Procedure>> readProcedures() async {
    final runs = [
      for (final e in _runs.entries)
        if (e.value.session.isNotEmpty)
          (taskId: e.key, taskTitle: _briefs[e.key]?['title'] as String? ?? e.key, session: e.value.session),
    ];
    final dir = procedureDir.path;
    return Isolate.run(() {
      final (minted, used) = ProcedureStore.provenance(runs);
      return ProcedureStore(Directory(dir)).read(mintedBy: minted, usedBy: used);
    });
  }

  Future<void> _playbook(List<String> args) async {
    final res = await runTool(playbookExecutable, args);
    if (res.exitCode != 0) {
      throw StateError('playbook ${args.first} failed: ${(res.stderr as String).trim()} ${(res.stdout as String).trim()}');
    }
  }

  ProcedureHistory get _history => ProcedureHistory(procedureDir);

  // ---- the desk's own files ------------------------------------------------

  /// The settings file this engine answers for: the same one the desktop
  /// app reads, so a browser and the desk agree.
  FileSettingsStore settingsStore = FileSettingsStore();

  File get _ackFile => _marksFile ?? File('${mizpahPaths().state}${pathSep}acknowledged.json');

  /// The desk's own paper: the Board's beside the marks, the Deputy's in
  /// its state dir (beside the marks too when a test passes its own).
  DeskNotices get _deskNotices => DeskNotices(
    boardDir: _boardDir ?? (engine.root.isEmpty ? null : Directory('${engine.root}${pathSep}notices${pathSep}board')),
    boardFile: File('${_ackFile.parent.path}${pathSep}board.json'),
    deputyFile: File(_marksFile != null ? '${_ackFile.parent.path}${pathSep}deputy-notices.jsonl' : '${_deputyDir.path}${pathSep}notices.jsonl'),
  );

  @override
  Future<StorageReport> measureStorage() {
    final root = _discovery?.root.path ?? settingsStore.defaultRunsRoot;
    return Isolate.run(() => scan.measureStorage(root));
  }

  @override
  Future<int> clearTranscripts() {
    final root = _discovery?.root.path ?? settingsStore.defaultRunsRoot;
    return Isolate.run(() => scan.clearTranscripts(root));
  }

  @override
  Future<Map<String, dynamic>> readAcknowledged() async {
    try {
      if (!_ackFile.existsSync()) return const {};
      return (jsonDecode(_ackFile.readAsStringSync()) as Map).cast<String, dynamic>();
    } catch (_) {
      return const {};
    }
  }

  @override
  Future<Map<String, dynamic>> markAcknowledged({
    List<String> read = const [],
    List<String> unread = const [],
    List<String> dismissed = const [],
  }) async {
    final j = await readAcknowledged();
    final r = {...((j['read'] as List?)?.cast<String>() ?? const [])}
      ..addAll(read)
      ..removeAll(unread);
    final d = {...((j['dismissed'] as List?)?.cast<String>() ?? const [])}..addAll(dismissed);
    final out = {'read': r.toList()..sort(), 'dismissed': d.toList()..sort()};
    _ackFile.parent.createSync(recursive: true);
    // Written whole then moved into place: a reader never sees half a file.
    final tmp = File('${_ackFile.path}.tmp');
    tmp.writeAsStringSync(jsonEncode(out));
    tmp.renameSync(_ackFile.path);
    return out;
  }

  @override
  Future<Map<String, dynamic>?> readAppSettings() => settingsStore.load();

  /// Called by a browser client; the desk's own settings object hears
  /// about it through [settingsChanged].
  @override
  Future<void> writeAppSettings(Map<String, dynamic> settings) async {
    await settingsStore.save(settings);
    settingsChanged?.call();
  }

  void Function()? settingsChanged;

  @override
  Future<String> storeSignatureImage(List<int> bytes, String ext) =>
      settingsStore.storeSignatureImage(Uint8List.fromList(bytes), ext);

  @override
  Future<Map<String, bool>> inspectPath(String path) async {
    final d = Directory(path);
    return {'exists': d.existsSync(), 'git': Directory('${d.path}$pathSep.git').existsSync()};
  }

  @override
  Future<EngineInfo> hello() async => EngineInfo(
        home: Platform.environment['HOME'] ?? Platform.environment['USERPROFILE'] ?? '',
        runsRoot: _discovery?.root.path ?? settingsStore.defaultRunsRoot,
      );

  @override
  Future<LibraryPolicy> readLibraryPolicy() async => ProcedureStore(procedureDir).policy();

  @override
  Future<void> setLibraryPolicy({bool? enabled, int? grace}) async {
    await _playbook([
      'library', 'policy',
      if (enabled != null) enabled ? '--on' : '--off',
      if (grace != null) ...['--grace', '$grace'],
      '--protect', 'mizpah-',
    ]);
    _changes.add(null);
  }

  @override
  Future<void> pinProcedure(String id, bool on) async {
    await _playbook(['library', on ? 'pin' : 'unpin', id]);
    _changes.add(null);
  }

  @override
  Future<void> retireProcedure(String id) async {
    await _playbook(['library', 'decay', id]);
    _changes.add(null);
  }

  @override
  Future<void> reviveProcedure(String id) async {
    await _playbook(['library', 'revive', id]);
    _changes.add(null);
  }

  /// A person opening a procedure on the page: as good as it turning up
  /// in a search. Quiet: no change event, the ledger moved but nothing
  /// the page shows did.
  @override
  Future<void> touchProcedure(String id) => _playbook(['library', 'touch', id, 'search']);

  @override
  Future<bool> commitProcedures(String message) async {
    final did = await _history.commit(message);
    _changes.add(null);
    return did;
  }

  @override
  Future<List<ProcedureVersion>> procedureLog(String id) => _history.log(id);

  @override
  Future<List<DiffHunkLine>> procedureDiff(String id, String sha) => _history.diff(id, sha);

  @override
  Future<bool> restoreProcedure(String id, String sha) async {
    final did = await _history.restore(id, sha);
    _changes.add(null);
    return did;
  }

  /// The playbook demands a description and a tag at birth; a person's
  /// draft gets placeholders they replace on the sheet. `--unsearched`
  /// because a person writing by hand is not a worker who skipped search.
  @override
  Future<String> createProcedure(String title) async {
    var id = _slug(title);
    if (id.isEmpty) throw ArgumentError('a title is needed');
    final dir = procedureDir;
    var n = 2;
    while (File('${dir.path}$pathSep$id.json').existsSync()) {
      id = '${_slug(title)}-${n++}';
    }
    await _playbook([
      'create', id,
      '--title', title,
      '--description', 'When to pick it — write this.',
      '--tags', 'draft',
      '--unsearched',
    ]);
    await commitProcedures('new procedure $id');
    return id;
  }

  @override
  Future<void> deleteProcedure(String id) async {
    await _playbook(['delete', id]);
    await commitProcedures('delete procedure $id');
  }

  @override
  Future<void> editProcedure(String id, {String? title, String? description, List<String>? tags}) => _playbook([
        'edit',
        id,
        if (title != null) ...['--title', title],
        if (description != null) ...['--description', description],
        if (tags != null) ...['--tags', tags.join(',')],
      ]);

  @override
  Future<void> editStep(String id, String stepTitle, {String? rename, String? do_}) => _playbook([
        'edit-step',
        id,
        '--title',
        stepTitle,
        if (rename != null) ...['--rename', rename],
        if (do_ != null) ...['--do', do_],
      ]);

  @override
  Future<void> addStep(String id, String title, String do_, {String? after}) => _playbook([
        'add-step',
        id,
        '--title',
        title,
        '--do',
        do_,
        if (after != null) ...['--after', after],
      ]);

  @override
  Future<void> removeStep(String id, String stepTitle) => _playbook(['remove-step', id, '--title', stepTitle]);

  @override
  Future<void> linkStep(String id, String stepTitle, String procedure) =>
      _playbook(['edit-step', id, '--title', stepTitle, '--procedure', procedure]);

  @override
  Future<void> moveStep(String id, String stepTitle, int to) =>
      _playbook(['move-step', id, '--title', stepTitle, '--to', '$to']);

  @override
  Future<List<AttentionItem>> readAttention() async {
    final out = <AttentionItem>[];
    // Every project, a few at a time: the ones whose paper is unchanged
    // answer from memory; the rest rebuild in parallel isolates.
    final ids = [for (final id in _runs.keys) if (_briefs[id] != null && !_runs[id]!.archived) id];
    const lanes = 6;
    for (var i = 0; i < ids.length; i += lanes) {
      final batch = ids.sublist(i, (i + lanes).clamp(0, ids.length));
      final read = await Future.wait([for (final id in batch) readInbox(id)]);
      for (final (k, docs) in read.indexed) {
        final id = batch[k];
        final summary = _summary(id, _briefs[id]!);
        for (final d in docs) {
          // The inbox carries what needs the person: a change request for
          // signature, and a task that stopped or finished. Work orders,
          // gates and anomalies are the task's own paper, in Daily work.
          final wants = d.awaitingSignature || d.kind == DocKind.closing || d.kind == DocKind.completion;
          if (wants) out.add(AttentionItem(project: summary, document: d));
        }
      }
    }
    // The desk's own paper — the Board's, the Deputy's — is not a project's;
    // it sits in the tray with the rest and is dismissed the same way.
    out.addAll(_deskNotices.read());
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
  Future<List<Map<String, String>>> environments() async {
    final res = await runTool(_pythonExecutable, ['-m', 'mizpah.draft', 'environments'], workingDirectory: engine.mizpahDir);
    final line = (res.stdout as String).trim().split('\n').where((l) => l.startsWith('[')).lastOrNull ?? '[]';
    try {
      return [
        for (final e in (jsonDecode(line) as List))
          if (e is Map) {'name': '${e['name'] ?? ''}', 'note': '${e['note'] ?? ''}', if (e['default'] == true) 'default': 'true'},
      ];
    } catch (_) {
      return const [];
    }
  }

  @override
  Future<void> setDefaultEnvironment(String name) async {
    final res = await runTool(_pythonExecutable, ['-m', 'mizpah.bases', 'default', name], workingDirectory: engine.mizpahDir);
    if (res.exitCode != 0) {
      throw StateError(((res.stderr as String).trim().isEmpty ? res.stdout : res.stderr).toString().trim());
    }
    _changes.add(null);
  }

  @override
  Future<Map<String, String>> readCrew(String id) async {
    final r = _runs[id];
    if (r == null || r.session.isEmpty) return const {};
    return RunInbox(project: Directory(r.project), session: Directory(r.session)).crew();
  }

  @override
  Future<List<InboxDocument>> readWorkOrders(String id) async {
    final r = _runs[id];
    if (r == null || r.session.isEmpty) return const [];
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
      final f = File('${stateDir(r.project)}/route.json');
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
    if (r == null || r.session.isEmpty) return const [];
    final inbox = RunInbox(project: Directory(r.project), session: Directory(r.session));
    // Same files as last time: the same paper, from memory. The stats
    // cost microseconds; spawning an isolate to re-read a cache does not.
    final fp = inbox.fingerprint();
    final held = _inboxHeld[r.session];
    if (held != null && held.$1 == fp) return held.$2;
    // Event logs are hundreds of megabytes; read off the UI isolate.
    final docs = await Isolate.run(() => RunInbox(project: Directory(r.project), session: Directory(r.session)).read());
    _inboxHeld[r.session] = (fp, docs);
    return docs;
  }

  /// Session root → (fingerprint, documents) as last read.
  final _inboxHeld = <String, (String, List<InboxDocument>)>{};

  /// Run projects found on disk; nothing is seeded — the console shows
  /// only what the engine has actually run.
  final Map<String, Map<String, dynamic>> _briefs = {};

  final _routes = <String, FakeRoute>{};

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
  String get _pythonExecutable => engine.python;

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
    if (mode != LoopMode.run) {
      // Hold the loop that is running, whichever session the desk shows:
      // a STOP in an old session's folder held nothing. None live is a no-op.
      final live = (_sessions[id] ?? const <RunSession>[]).where((s) => s.running).toList();
      for (final s in live) {
        File('${s.path}/STOP').writeAsStringSync('hold from the app ${DateTime.now().toIso8601String()}\n');
      }
      _changes.add(null);
      return;
    }
    // Resume the shown session — and only when nothing of this project is
    // live, so a second loop never starts beside the first. Said, not
    // swallowed: it used to return quietly and the button looked broken.
    if (run.running || (_sessions[id] ?? const <RunSession>[]).any((s) => s.running)) {
      throw StateError('a loop is already live on this project; hold it first');
    }
    final stop = File('${run.session}/STOP');
    if (stop.existsSync()) stop.deleteSync();
    await _resumeIfStopped(run);
  }

  /// A run whose loop is not alive is relaunched on its own session root:
  /// its tasks resume, and the brief it routes against is the one on disk
  /// now (with the decision just made).
  @override
  Future<void> replyToRun(String id, String text, {bool resume = false}) async {
    final run = _runs[id];
    if (run == null || run.session.isEmpty) throw StateError('no session to reply to');
    if (text.trim().isEmpty) return;
    // The record says what the reply did: a note alone, or a note that
    // relaunched the run. The engine reads `at` and `text`; the desk shows the rest.
    File('${run.session}/operator.jsonl').writeAsStringSync(
      '${jsonEncode({'at': DateTime.now().millisecondsSinceEpoch / 1000, 'text': text.trim(), if (resume) 'resume': true})}\n',
      mode: FileMode.append,
    );
    if (resume) {
      final stop = File('${run.session}/STOP');
      if (stop.existsSync()) stop.deleteSync();
      await _resumeIfStopped(run);
    }
    _changes.add(null);
  }

  @override
  Future<void> memoToWorker(String id, String task, String text) async {
    final run = _runs[id];
    if (run == null || run.session.isEmpty) throw StateError('no session to write to');
    if (text.trim().isEmpty) return;
    final taskDir = Directory('${run.session}/tasks/$task');
    if (!taskDir.existsSync()) throw StateError('no worker has sat down on $task');
    // The worker's harness reads nudge.md at its next boundary and renames
    // it delivered; a memo before it was read is appended, not lost.
    final nudge = File('${taskDir.path}/nudge.md');
    nudge.writeAsStringSync('${nudge.existsSync() ? '${nudge.readAsStringSync().trimRight()}\n\n' : ''}${text.trim()}\n');
    File('${run.session}/operator.jsonl').writeAsStringSync(
      '${jsonEncode({'at': DateTime.now().millisecondsSinceEpoch / 1000, 'text': text.trim(), 'to': 'worker:$task'})}\n',
      mode: FileMode.append,
    );
    _changes.add(null);
  }

  Future<void> _resumeIfStopped(RunProject run) async {
    if (run.running) return;
    // The same path as a first start: the app's engine config, with the crew
    // the project pinned (its .mizpah/config.json) layered over it. A run no
    // longer names a config file of its own; the per-model files drifted.
    if (engineConfig.isEmpty) {
      throw StateError('no engine config set; cannot resume ${run.id}');
    }
    final engineDir = engine.mizpahDir;
    await Process.start(_pythonExecutable, [
      '-m',
      'mizpah.loop',
      '--config',
      engineConfig,
      '--project',
      run.project,
      '--root',
      run.session,
      '--max-cycles',
      '40',
      '--max-tasks',
      '60',
    ], workingDirectory: engineDir, mode: ProcessStartMode.detached);
    _changes.add(null);
  }

  @override
  Stream<List<Turn>> watchTranscript(String id, String session) {
    final r = _runs[id];
    if (r == null) return (_loops[id] ??= FakeLoop()).transcript(session);
    // Tail the log every two seconds while someone is watching; stop when
    // they look away. A finished session still yields once.
    // Only plain strings cross into the isolate: a closure over this scope
    // would drag the Timer along, which cannot be sent.
    final args = (r.project, r.session, session);
    // The journal behind the seat: a stat says whether it moved, so a
    // quiet session costs nothing every two seconds (a 74 MB journal was
    // parsed in a fresh isolate on every tick, watched or not moving).
    final journal = File(session.startsWith('controller')
        ? '${r.session}/controller.jsonl'
        : session == 'host'
        ? '${r.session}/host.live.json'
        : '${r.session}/tasks/$session/events/session.jsonl');
    // A controller step in progress moves this file, not the journal.
    final live = File('${r.session}/controller.live.json');
    late StreamController<List<Turn>> c;
    Timer? t;
    String last = '';
    String stamp = '';
    Future<void> tick() async {
      final st = journal.existsSync() ? journal.statSync() : null;
      final lv = session.startsWith('controller') && live.existsSync() ? live.statSync() : null;
      final now = '${st == null ? '-' : '${st.modified.millisecondsSinceEpoch}:${st.size}'}'
          '|${lv == null ? '-' : '${lv.modified.millisecondsSinceEpoch}:${lv.size}'}';
      if (now == stamp) return;
      stamp = now;
      final turns = await _readTurnsInIsolate(args);
      final sig = '${turns.length}:${turns.isEmpty ? '' : turns.last.title}${turns.isEmpty ? '' : turns.last.body.length}';
      if (sig != last && !c.isClosed) {
        last = sig;
        c.add(turns);
      }
    }
    c = StreamController<List<Turn>>(
      onListen: () {
        tick();
        t = Timer.periodic(const Duration(seconds: 2), (_) => tick());
      },
      // The stream ends with its last listener: the timer and the sink go together.
      onCancel: () {
        t?.cancel();
        c.close();
      },
    );
    return c.stream;
  }

  @override
  Future<TraceWindow> readTraceWindow(String id, String session, {int? end, int? from, int? ifLength}) async {
    final r = _runs[id];
    if (r == null || r.session.isEmpty) return const TraceWindow(turns: [], start: 0, end: 0, fileLength: 0);
    if (ifLength != null) {
      // A stat, not a read: the tail poll asks every two seconds.
      final f = File('${r.session}/tasks/$session/events/session.jsonl');
      final length = f.existsSync() ? f.lengthSync() : 0;
      if (length == ifLength) {
        return TraceWindow(turns: const [], start: from ?? 0, end: length, fileLength: length, unchanged: true, wire: _wire(r.session, session));
      }
    }
    final w = await _readWindowInIsolate((r.project, r.session, session, end, from));
    return TraceWindow(turns: w.turns, start: w.start, end: w.end, fileLength: w.fileLength, wire: _wire(r.session, session));
  }

  @override
  Future<List<EnvironmentInfo>> readEnvironmentDetails() {
    // Every task the desk knows, by the environment its brief names.
    final tasks = <String, String?>{
      for (final e in _briefs.entries) e.key: (e.value['environment'] as String?),
    };
    return Isolate.run(() => readEnvironments(tasks: tasks));
  }

  String _taskRoot(String id) {
    final path = _paths[id] ?? _runs[id]?.project;
    if (path == null || path.isEmpty) throw ArgumentError('no task $id');
    return path;
  }

  @override
  Future<void> openTaskInEditor(String id, [String path = '']) async {
    final root = _taskRoot(id);
    await env_fs.openPathInEditor(root, env_fs.insideRoot(root, path) ?? root);
  }

  @override
  Future<List<FileNode>> listTaskFiles(String id, String path) async => env_fs.listDirUnder(_taskRoot(id), path);

  @override
  Future<EnvFile> readTaskFile(String id, String path) async => env_fs.readFileUnder(_taskRoot(id), path);

  @override
  Future<List<int>> readTaskFileBytes(String id, String path) async => env_fs.readBytesUnder(_taskRoot(id), path);

  @override
  Future<List<FileNode>> listEnvironmentFiles(String name, String path) async => listEnvironmentDir(name, path);

  @override
  Future<EnvFile> readEnvironmentFile(String name, String path) async => env_fs.readEnvironmentFile(name, path);
  @override
  Future<void> createEnvironment(String name) async => env_fs.createEnvironment(name);
  @override
  Future<void> openEnvironmentInEditor(String name, String path) => env_fs.openInEditor(name, path);

  @override
  Future<List<Turn>> readTraceRules(String id, String session, {required int before}) async {
    final r = _runs[id];
    if (r == null || r.session.isEmpty || before <= 0) return const [];
    final (project, root) = (r.project, r.session);
    return Isolate.run(() => RunFloor(project: Directory(project), session: Directory(root))
        .workerWindow(session, from: 0, end: before, rulesOnly: true)
        .turns);
  }

  static Wire? _wire(String session, String task) => Wire.read(File('$session/tasks/$task/events/stream.json'));

  @override
  Future<List<FloorSession>> readSessions(String id) async {
    final r = _runs[id];
    if (r == null || r.session.isEmpty) return const [];
    return Isolate.run(
      () => RunFloor(project: Directory(r.project), session: Directory(r.session)).sessions(),
    );
  }

  final _paths = <String, String>{};

  @override
  Future<List<BriefSummary>> listBriefs() async => [
    for (final e in _briefs.entries) _summary(e.key, e.value),
  ];

  /// A run's state is its loop's: live while the process is up, completed
  /// on a completed run, stopped for any other end. Attention is the number of
  /// change requests awaiting signature — nothing else.
  BriefSummary _summary(String id, Map<String, dynamic> brief) {
    final r = _runs[id];
    var state = 'idle';
    DateTime? endedAt;
    var attention = (brief['proposals'] as List? ?? const [])
        .where((p) => (p as Map)['status'] == 'open')
        .length;
    if (r != null && r.session.isEmpty) {
      // A draft: written, never run. The Deputy's drafts and projects made
      // by `mizpah init` are one state; the id says who holds it.
      state = 'idle';
    } else if (r != null) {
      String? stop;
      // The row's state is the project's newest run, whichever session the
      // desk is showing: browsing history must not repaint the sidebar.
      final newest = (_sessions[id] ?? const <RunSession>[]).firstOrNull;
      final f = File('${newest?.path ?? r.session}/loop.json');
      if (f.existsSync()) {
        if (!r.running) endedAt = f.lastModifiedSync().toUtc();
        try {
          final loop = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
          stop = loop['stop'] as String?;
        } catch (_) {}
      }
      // A stall is a system alert: it goes to Home as an anomaly report,
      // not into the sidebar count, which is signatures owed only.
      state = r.archived
          ? 'archived'
          : r.running
          ? 'live'
          : (stop == 'completed' || stop == 'nothing_owed')
          ? 'completed'
          : 'stopped'; // any other reason, or a loop killed before it wrote one
    }
    return BriefSummary(
      id: id,
      title: brief['title'] as String? ?? id,
      path: _paths[id] ?? '',
      state: state,
      attention: attention,
      endedAt: endedAt,
    );
  }

  @override
  Future<Map<String, dynamic>> readBrief(String id) async {
    final b = _briefs[id];
    if (b == null) throw ArgumentError.value(id, 'id', 'no such brief');
    return b;
  }

  /// A run project's brief is Terra's file: the edit goes through
  /// `terra brief set --replace-lists` in the project directory (version
  /// bump, and a dropped entry renumbers phases and map cites the way an
  /// accepted removal does) and the draft is re-read from disk. A live
  /// loop sees the new brief at its next step. Enablers and phases are
  /// not written here: their state belongs to the loop.
  @override
  Future<void> writeBrief(String id, Map<String, dynamic> brief) async {
    final current = _briefs[id];
    if (current == null) throw ArgumentError.value(id, 'id', 'no such brief');
    if (_canonical(brief) == _canonical(current)) return;
    final run = _runs[id];
    if (run == null) {
      final stored = Map<String, dynamic>.of(brief);
      stored['version'] = (current['version'] as int? ?? 1) + 1;
      _briefs[id] = stored;
      return;
    }
    List<String> strings(Object? v) => [for (final e in (v as List? ?? const [])) '$e'];
    final args = <String>[
      'set',
      '--replace-lists',
      '--title', '${brief['title'] ?? ''}',
      '--mission', '${brief['mission'] ?? ''}',
      for (final n in strings(brief['needs'])) ...['--need', n],
      for (final n in strings(brief['non_goals'])) ...['--non-goal', n],
      for (final d in strings(brief['deliverables'])) ...['--deliverable', d],
      if (brief['budget_points'] is int) ...['--budget-points', '${brief['budget_points']}']
      else if (current['budget_points'] != null) '--clear-budget-points',
      '--budget-notes', '${brief['budget_notes'] ?? ''}',
    ];
    await _terraBrief(run, args);
  }

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
  String get terraExecutable => engine.terra;

  Future<void> _terraBrief(RunProject r, List<String> args) async {
    // The project's state dir may be `.mizpah/` (new) or `.terra/` (older):
    // terra is told which, as startTask does.
    final res = await runTool(terraExecutable, [
      'brief',
      ...args,
    ], workingDirectory: r.project, environment: {...Platform.environment, 'TERRA_DIRNAME': stateDirName(r.project)});
    if (res.exitCode != 0) {
      throw StateError(
        'terra brief ${args.join(' ')} failed: ${(res.stderr as String).trim()} ${(res.stdout as String).trim()}',
      );
    }
    final bf = File('${stateDir(r.project)}/brief.json');
    _briefs[r.id] = jsonDecode(bf.readAsStringSync()) as Map<String, dynamic>;
    _briefStamp.remove(r.id);
    _changes.add(null);
  }

  /// A run project: `terra brief accept` in its directory (the brief on
  /// disk changes; a running loop picks it up at its next step). A seeded
  /// demo brief: mirrors terra.brief.accept_proposal in memory.
  @override
  Future<void> acceptProposal(String id, String proposalId, {String reason = ''}) async {
    final run = _runs[id];
    if (run != null) {
      _proposal(id, proposalId); // refuses an already-decided proposal
      await _terraBrief(run, ['accept', proposalId, if (reason.trim().isNotEmpty) ...['--reason', reason.trim()], ...await _signedBy(run.project)]);
      await _resumeIfStopped(run);
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
      if (patch[e.key] != null) push(e.value, patch[e.key] as Object);
    }
    if (patch['mission'] != null) b['mission'] = patch['mission'];
    p['status'] = 'accepted';
    b['version'] = (b['version'] as int? ?? 1) + 1;
  }

  @override
  Future<void> rejectProposal(String id, String proposalId, {String reason = ''}) async {
    final run = _runs[id];
    if (run != null) {
      _proposal(id, proposalId);
      await _terraBrief(run, ['reject', proposalId, if (reason.trim().isNotEmpty) ...['--reason', reason.trim()], ...await _signedBy(run.project)]);
      await _resumeIfStopped(run);
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
    this.archived = false,
  });
  final String id;
  final String project;
  final String session;
  final bool running;
  final bool archived;
}

/// Spawns the read from a scope that holds nothing but [args]: a closure
/// made inside the stream's `tick` would capture its Timer too, and a
/// Timer cannot cross into an isolate.
Future<List<Turn>> _readTurnsInIsolate((String, String, String) args) =>
    Isolate.run(() => _readTurns(args));

/// Runs in the isolate: (project, session root, seat) → the seat's turns.
List<Turn> _readTurns((String, String, String) args) {
  final (project, session, seat) = args;
  final floor = RunFloor(project: Directory(project), session: Directory(session));
  if (seat.startsWith('controller')) {
    return floor.controllerTurns(decision: int.tryParse(seat.split(':').last));
  }
  if (seat == 'host') return floor.hostTurns();
  return floor.workerTurns(seat);
}

Future<TraceWindow> _readWindowInIsolate((String, String, String, int?, int?) args) =>
    Isolate.run(() {
      final (project, session, seat, end, from) = args;
      return RunFloor(project: Directory(project), session: Directory(session))
          .workerWindow(seat, end: end, from: from);
    });
