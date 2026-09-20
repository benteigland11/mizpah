import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/host_watch.dart';

/// A child that starts a grandchild and exits: the grandchild is reparented
/// to PID 1 carrying the mark, exactly what a probe's browser looks like
/// after the probe was killed.
Future<int> orphan(String root) async {
  final r = await Process.run(
    'python3',
    [
      '-c',
      'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
          'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)',
    ],
    environment: {HostWatch.mark: root},
  );
  final pid = int.parse((r.stdout as String).trim());
  for (var i = 0; i < 50; i++) {
    final stat = File('/proc/$pid/stat').readAsStringSync();
    if (stat.substring(stat.lastIndexOf(')') + 2).split(' ')[1] == '1') break;
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  return pid;
}

void main() {
  test('the app kills marked orphans and logs them', () async {
    if (!Platform.isLinux) return;
    final dir = Directory.systemTemp.createTempSync('hostwatch');
    final pid = await orphan('${dir.path}/run');
    final watch = HostWatch(runsRoot: dir, poll: const Duration(hours: 1));
    await Future<void>.delayed(const Duration(seconds: 3));
    watch.dispose();
    expect(watch.reapedTotal, greaterThanOrEqualTo(1));
    expect(watch.reaped.any((r) => r.process.pid == pid), isTrue);
    expect(Directory('/proc/$pid').existsSync(), isFalse);
    expect(File('${dir.path}/host-leaks.jsonl').readAsStringSync(), contains('"pid":$pid'));
    dir.deleteSync(recursive: true);
  });
}
