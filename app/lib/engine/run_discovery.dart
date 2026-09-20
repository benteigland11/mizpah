import 'dart:convert';
import 'dart:io';

/// A run found on disk: the Terra project and the session root the loop
/// wrote, and whether that loop is still alive.
class DiscoveredRun {
  const DiscoveredRun({
    required this.id,
    required this.project,
    required this.session,
    required this.running,
  });
  final String id;
  final String project;
  final String session;
  final bool running;
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

  static File defaultRegistry() {
    final env = Platform.environment;
    final base = env['XDG_STATE_HOME'] ?? '${env['HOME']}/.local/state';
    return File('$base/mizpah/runs.jsonl');
  }

  static const _recent = Duration(minutes: 10);

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
    }
    found.sort((a, b) => a.id.compareTo(b.id));
    return found;
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
    // archived in place: `archived: true` in run.json and it leaves the
    // console, files untouched.
    if (record?['archived'] == true) return null;
    var project = record?['project'] as String?;
    if (project == null || !Directory('$project/.terra').existsSync()) {
      project = _byConvention(session);
    }
    if (project == null) return null;

    bool running;
    if (record != null && record['ended_at'] == null && record['pid'] is int) {
      running = Directory('/proc/${record['pid']}').existsSync();
    } else if (record != null) {
      running = false;
    } else {
      Map<String, dynamic>? l;
      try {
        if (loop.existsSync()) l = jsonDecode(loop.readAsStringSync()) as Map<String, dynamic>;
      } catch (_) {}
      final stopped = l == null || l['stop'] != null;
      running =
          !stopped &&
          DateTime.now().difference(loop.statSync().modified) < _recent;
    }

    final rel = project.startsWith(root.path)
        ? project.substring(root.path.length).replaceFirst(RegExp(r'^/'), '')
        : project;
    return DiscoveredRun(
      id: rel,
      project: project,
      session: session.path,
      running: running,
    );
  }

  String? _byConvention(Directory session) {
    final parent = session.parent;
    final name = session.uri.pathSegments.where((s) => s.isNotEmpty).last;
    if (name.endsWith('-sess')) {
      final p = '${parent.path}/${name.substring(0, name.length - 5)}';
      if (Directory('$p/.terra').existsSync()) return p;
    }
    // `sess` beside the one project directory.
    final candidates = <String>[];
    for (final e in parent.listSync(followLinks: false)) {
      if (e is Directory && Directory('${e.path}/.terra').existsSync()) {
        candidates.add(e.path);
      }
    }
    return candidates.length == 1 ? candidates.first : null;
  }
}
