import 'dart:convert';
import 'dart:io';

import '../cg/run_liveness/run_liveness.dart';
import 'platform_paths.dart';
import 'state_dir.dart';

/// A run found on disk: the Terra project and the session root the loop
/// wrote, and whether that loop is still alive.
class DiscoveredRun {
  const DiscoveredRun({
    required this.id,
    required this.project,
    required this.session,
    required this.running,
    this.archived = false,
    this.startedAt,
    this.stop,
  });
  final String id;
  final String project;
  final String session;
  final bool running;

  /// When the loop started (run.json), else the session's own stamp; a
  /// project with several sessions is ordered by this.
  final DateTime? startedAt;

  /// Why the loop stopped (`completed`, `controller_stalled`, …); null
  /// while it runs or when the record predates it.
  final String? stop;

  /// `archived: true` in run.json: a superseded drill or smoke test. It
  /// stays walkable at the foot of the sidebar and off Home.
  final bool archived;

  /// A task that has never run: no session root yet.
  bool get isDraft => session.isEmpty;
}

/// Finds runs under a root. A session root is any directory holding
/// `loop.json`. Its project comes from `run.json` when the engine wrote
/// one; older sessions fall back to the naming convention (`X-sess` beside
/// `X`, `sess` beside the one directory with `.terra`). Alive means the
/// recorded pid still exists, or — without a record — the loop has not
/// stopped and wrote within the last few minutes.
class RunDiscovery {
  RunDiscovery(this.root, {this.maxDepth = 3, File? registry})
    : registry = registry ?? defaultRegistry();
  final Directory root;
  final int maxDepth;

  /// The engine appends every run it starts here, wherever the run lives
  /// (`mizpah.loop.registry_path`). Scanning [root] catches runs started
  /// before the registry existed.
  final File registry;

  static File defaultRegistry() => File('${mizpahPaths().state}${pathSep}runs.jsonl');

  /// Every project `mizpah init` made on this machine, wherever it lives:
  /// how a task shows up before its first run writes a session.
  static File projectsRegistry() => File('${mizpahPaths().state}${pathSep}projects.jsonl');

  /// Projects with no repository of the person's (`mizpah init --gym`),
  /// and the Deputy's drafts (`gyms/<slug>`, a brief in draft status).
  static Directory gymsRoot() => Directory('${mizpahPaths().data}${pathSep}gyms');

  /// Legacy records (no heartbeat) get the OS asked about their pid where
  /// that is cheap and certain; elsewhere the file's age decides.
  static bool? _pidAlive(int pid) =>
      Platform.isLinux ? Directory('/proc/$pid').existsSync() : null;

  List<DiscoveredRun> scan() {
    final found = <DiscoveredRun>[];
    final seen = <String>{};
    if (registry.existsSync()) {
      for (final line in registry.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        try {
          final r = jsonDecode(line) as Map<String, dynamic>;
          final session = Directory(r['root'] as String);
          if (!_isSession(session) || !seen.add(session.path)) continue;
          final d = _resolve(session);
          if (d != null) found.add(d);
        } catch (_) {}
      }
    }
    if (root.existsSync()) {
      final scanned = <DiscoveredRun>[];
      _walk(root, 0, scanned);
      for (final d in scanned) {
        if (seen.add(d.session)) found.add(d);
      }
      // Tasks that exist but have never run: a `.terra/brief.json` with no
      // session pointing at it. They are drafts; the session comes later.
      final claimed = {for (final d in found) d.project};
      final drafts = <DiscoveredRun>[];
      _walkDrafts(root, 0, claimed, drafts);
      found.addAll(drafts);
    }
    // Projects registered by `mizpah init` and gyms: every session under
    // their `.mizpah/sessions/` (the walk skips dot directories, and the
    // registry knows only the runs the loop announced), else a draft.
    final claimed = {for (final d in found) d.project};
    void draft(String project) {
      if (!File('${stateDir(project)}/brief.json').existsSync()) return;
      final sessions = Directory('${stateDir(project)}/sessions');
      if (sessions.existsSync()) {
        for (final e in sessions.listSync(followLinks: false)) {
          if (e is! Directory || !_isSession(e) || !seen.add(e.path)) continue;
          final d = _resolve(e);
          if (d != null) {
            found.add(d);
            claimed.add(project);
          }
        }
      }
      if (claimed.contains(project)) return;
      claimed.add(project);
      found.add(DiscoveredRun(id: _idFor(project), project: project, session: '', running: false));
    }
    for (final project in claimed.toList()) {
      draft(project);
    }
    // A draft row is for a project with no run; drop it once one is found.
    final ran = {for (final d in found) if (d.session.isNotEmpty) d.project};
    found.removeWhere((d) => d.session.isEmpty && ran.contains(d.project));
    final pr = projectsRegistry();
    if (pr.existsSync()) {
      for (final line in pr.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        try {
          final r = jsonDecode(line) as Map<String, dynamic>;
          final project = r['project'] as String?;
          if (project != null && Directory(project).existsSync()) draft(project);
        } catch (_) {}
      }
    }
    final gyms = gymsRoot();
    if (gyms.existsSync()) {
      for (final e in gyms.listSync(followLinks: false)) {
        if (e is Directory) draft(e.path);
      }
    }
    found.sort((a, b) => a.id.compareTo(b.id));
    return found;
  }

  /// A project's id: its path relative to the runs root when under it,
  /// else `gyms/<name>` for a gym, else the full path.
  String _idFor(String project) {
    if (project.startsWith(root.path)) {
      return project.substring(root.path.length).replaceFirst(RegExp(r'^/'), '');
    }
    final g = gymsRoot().path;
    if (project.startsWith(g)) return 'gyms/${project.substring(g.length).replaceFirst(RegExp(r'^/'), '')}';
    return project;
  }

  void _walkDrafts(Directory dir, int depth, Set<String> claimed, List<DiscoveredRun> out) {
    List<FileSystemEntity> entries;
    try {
      entries = dir.listSync(followLinks: false);
    } catch (_) {
      return;
    }
    for (final e in entries) {
      if (e is! Directory) continue;
      final name = e.uri.pathSegments.where((s) => s.isNotEmpty).last;
      if (name.startsWith('.') || name.startsWith('tmp') || _isSession(e)) continue;
      if (File('${stateDir(e.path)}/brief.json').existsSync()) {
        if (!claimed.contains(e.path)) {
          final rel = e.path.startsWith(root.path)
              ? e.path.substring(root.path.length).replaceFirst(RegExp(r'^/'), '')
              : e.path;
          out.add(DiscoveredRun(id: rel, project: e.path, session: '', running: false));
        }
        continue; // a task holds no tasks
      }
      if (depth + 1 < maxDepth) _walkDrafts(e, depth + 1, claimed, out);
    }
  }

  void _walk(Directory dir, int depth, List<DiscoveredRun> out) {
    List<FileSystemEntity> entries;
    try {
      entries = dir.listSync(followLinks: false);
    } catch (_) {
      return;
    }
    for (final e in entries) {
      if (e is! Directory) continue;
      final name = e.uri.pathSegments.where((s) => s.isNotEmpty).last;
      if (name.startsWith('.') || name.startsWith('tmp')) continue;
      if (_isSession(e)) {
        final r = _resolve(e);
        if (r != null) out.add(r);
        continue; // a session root has no runs beneath it
      }
      if (depth + 1 < maxDepth) _walk(e, depth + 1, out);
    }
  }

  /// A session root has `run.json` from its first second, or `loop.json`
  /// from an older engine that only wrote at cycle end.
  static bool _isSession(Directory d) =>
      File('${d.path}/run.json').existsSync() || File('${d.path}/loop.json').existsSync();

  DiscoveredRun? _resolve(Directory session) {
    final loop = File('${session.path}/loop.json');
    Map<String, dynamic>? record;
    final rf = File('${session.path}/run.json');
    if (rf.existsSync()) {
      try {
        record = jsonDecode(rf.readAsStringSync()) as Map<String, dynamic>;
      } catch (_) {}
    }
    // A superseded run (a drill re-run on a newer engine, a smoke test) is
    // archived in place: `archived: true` in run.json. Files untouched; it
    // folds to the foot of the sidebar and stays off Home.
    final archived = record?['archived'] == true;
    var project = record?['project'] as String?;
    if (project == null || !isProject(project)) {
      // A session inside a project's own state folder is that project's,
      // whatever run.json names: a copied or moved project (an archive of a
      // gym) kept its sessions, and its record still points at the old path.
      final state = session.parent.parent;
      final home = state.parent.path;
      project = session.parent.uri.pathSegments.where((s) => s.isNotEmpty).last == 'sessions' &&
              stateDir(home) == state.path && isProject(home)
          ? home
          : _byConvention(session);
    }
    if (project == null) return null;

    // Alive means the loop's heartbeat is fresh. Records from before
    // heartbeats fall back to a pid probe (Linux) or the file's age; a
    // session with no run.json at all is judged by loop.json.
    bool running;
    if (record != null) {
      final stamp = rf.existsSync() ? rf.statSync().modified : null;
      final verdict = judgeLiveness(
        record,
        now: DateTime.now(),
        pidAlive: Platform.isLinux ? (pid) => _pidAlive(pid) ?? false : null,
        lastWrite: stamp,
      );
      running = verdict == RunLiveness.live;
    } else {
      Map<String, dynamic>? l;
      try {
        if (loop.existsSync()) l = jsonDecode(loop.readAsStringSync()) as Map<String, dynamic>;
      } catch (_) {}
      final stopped = l == null || l['stop'] != null;
      running = !stopped &&
          judgeLiveness(const {}, now: DateTime.now(), lastWrite: loop.statSync().modified) ==
              RunLiveness.live;
    }

    // The same id a draft of this project has, so a project keeps its id
    // when its first run starts (a gym was `gyms/<name>` as a draft and
    // its full path once run: the desk lost it on signing and went home).
    final rel = _idFor(project);
    final startedAt = record?['started_at'];
    String? stop = record?['stop'] as String?;
    if (record == null) {
      try {
        if (loop.existsSync()) stop = (jsonDecode(loop.readAsStringSync()) as Map)['stop'] as String?;
      } catch (_) {}
    }
    return DiscoveredRun(
      id: rel,
      project: project,
      session: session.path,
      running: running && !archived,
      archived: archived,
      startedAt: startedAt is num
          ? DateTime.fromMillisecondsSinceEpoch((startedAt * 1000).round())
          : (rf.existsSync() ? rf : loop).statSync().modified,
      stop: stop,
    );
  }

  String? _byConvention(Directory session) {
    final parent = session.parent;
    final name = session.uri.pathSegments.where((s) => s.isNotEmpty).last;
    if (name.endsWith('-sess')) {
      final p = '${parent.path}/${name.substring(0, name.length - 5)}';
      if (isProject(p)) return p;
    }
    // `sess` beside the one project directory.
    final candidates = <String>[];
    for (final e in parent.listSync(followLinks: false)) {
      if (e is Directory && isProject(e.path)) {
        candidates.add(e.path);
      }
    }
    return candidates.length == 1 ? candidates.first : null;
  }
}
