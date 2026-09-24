import 'dart:convert';
import 'dart:io';

import '../cg/run_liveness/run_liveness.dart';
import '../models/storage.dart';

/// Weigh the tasks folder and clear worker transcripts. Engine side: runs
/// where the files are, inside an isolate when called from the app.
const transcriptName = 'session.jsonl';

/// The same judgement discovery makes: a fresh heartbeat, or for older
/// records a pid probe where the OS makes that cheap.
bool _live(Directory session) {
  final f = File('${session.path}/run.json');
  if (!f.existsSync()) return false;
  try {
    final record = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
    return judgeLiveness(
          record,
          now: DateTime.now(),
          pidAlive: Platform.isLinux ? (pid) => Directory('/proc/$pid').existsSync() : null,
          lastWrite: f.statSync().modified,
        ) ==
        RunLiveness.live;
  } catch (_) {
    return false;
  }
}

/// A transcript's session root is three levels up: `sess/tasks/id/events/`.
Directory? _sessionOf(File transcript) {
  final events = transcript.parent;
  if (events.uri.pathSegments.where((s) => s.isNotEmpty).last != 'events') return null;
  return events.parent.parent.parent;
}

StorageReport measureStorage(String root) {
  final dir = Directory(root);
  var total = 0, transcripts = 0, files = 0, live = 0;
  final liveCache = <String, bool>{};
  if (dir.existsSync()) {
    for (final e in dir.listSync(recursive: true, followLinks: false)) {
      if (e is! File) continue;
      int size;
      try {
        size = e.lengthSync();
      } catch (_) {
        continue;
      }
      total += size;
      if (e.uri.pathSegments.last == transcriptName) {
        transcripts += size;
        files++;
        final sess = _sessionOf(e);
        if (sess != null && (liveCache[sess.path] ??= _live(sess))) live += size;
      }
    }
  }
  return StorageReport(
    total: total,
    transcripts: transcripts,
    transcriptFiles: files,
    liveTranscripts: live,
    measuredAt: DateTime.now(),
  );
}

int clearTranscripts(String root) {
  final dir = Directory(root);
  var freed = 0;
  final liveCache = <String, bool>{};
  if (!dir.existsSync()) return 0;
  for (final e in dir.listSync(recursive: true, followLinks: false)) {
    if (e is! File || e.uri.pathSegments.last != transcriptName) continue;
    final sess = _sessionOf(e);
    if (sess == null || (liveCache[sess.path] ??= _live(sess))) continue;
    try {
      final n = e.lengthSync();
      e.deleteSync();
      freed += n;
    } catch (_) {}
  }
  return freed;
}

