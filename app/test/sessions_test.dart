import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/local_engine.dart';
import 'package:mizpah_app/engine/run_discovery.dart';

/// A project laid out the embedded way: `.mizpah/` with a brief, and
/// several runs under `.mizpah/sessions/<stamp>`.
Directory project(Directory root, String name, List<(String, num, String?)> sessions) {
  final p = Directory('${root.path}/$name')..createSync(recursive: true);
  Directory('${p.path}/.mizpah').createSync();
  File('${p.path}/.mizpah/brief.json').writeAsStringSync(jsonEncode({'title': name, 'proposals': []}));
  for (final (stamp, started, stop) in sessions) {
    final s = Directory('${p.path}/.mizpah/sessions/$stamp')..createSync(recursive: true);
    File('${s.path}/run.json').writeAsStringSync(jsonEncode({
      'project': p.path,
      'root': s.path,
      'pid': null,
      'started_at': started,
      'ended_at': started + 100,
      'stop': stop,
    }));
    File('${s.path}/loop.json').writeAsStringSync(jsonEncode({'stop': stop}));
  }
  return p;
}

void main() {
  test('a project with several sessions is one row showing the newest, and another can be chosen', () async {
    final root = Directory.systemTemp.createTempSync('mz_sessions');
    addTearDown(() => root.deleteSync(recursive: true));
    project(root, 'embed', [
      ('20260920T035532Z', 1789876532, 'nothing_owed'),
      ('20260920T041606Z', 1789877766, 'controller_stalled'),
    ]);
    final engine = LocalEngine(runsRoot: root, poll: const Duration(days: 1));
    addTearDown(engine.dispose);
    // Discovery also lists this machine's registered projects; look at ours.
    final briefs = (await engine.listBriefs()).where((b) => b.id == 'embed').toList();
    final id = briefs.single.id;

    expect(RunDiscovery(root).scan().where((r) => r.id == 'embed').length, 2,
        reason: 'one row per session and no draft row beside them');
    final sessions = engine.sessions(id);
    expect(sessions.map((s) => s.name), ['20260920T041606Z', '20260920T035532Z']);
    expect(sessions.first.stop, 'controller_stalled');
    expect(engine.currentSession(id), endsWith('20260920T041606Z'));

    var changed = 0;
    final sub = engine.changes.listen((_) => changed++);
    addTearDown(sub.cancel);
    engine.chooseSession(id, sessions.last.path);
    await Future<void>.delayed(Duration.zero);
    expect(engine.currentSession(id), endsWith('20260920T035532Z'));
    expect(changed, 1);
    // The project's state is the newest run's; the choice only moves the desk.
    expect((await engine.listBriefs()).firstWhere((b) => b.id == id).state, 'stopped');
  });
}
