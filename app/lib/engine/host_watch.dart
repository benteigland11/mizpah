import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

/// One process on this host that a Mizpah loop started (it carries
/// `MIZPAH_LOOP=<session root>` in its environment, inherited down to a
/// browser a probe launched).
class HostProcess {
  const HostProcess({
    required this.pid,
    required this.ppid,
    required this.comm,
    required this.cmdline,
    required this.root,
    required this.rssMb,
    required this.ageSeconds,
  });
  final int pid;
  final int ppid;
  final String comm;
  final String cmdline;

  /// The session root of the loop it belongs to.
  final String root;
  final int rssMb;
  final int ageSeconds;

  /// The loop process itself: detached by the app, so parent 1 by design.
  bool get isLoop => cmdline.contains('mizpah.loop');

  /// Reparented to PID 1 and not a loop: whatever started it is gone.
  bool get orphaned => ppid == 1 && !isLoop;

  Map<String, dynamic> toJson() => {
    'pid': pid,
    'comm': comm,
    'cmdline': cmdline,
    'root': root,
    'rss_mb': rssMb,
    'age_s': ageSeconds,
  };
}

/// A leak the app caught and killed.
class Reaped {
  const Reaped(this.process, this.at, {this.forced = false});
  final HostProcess process;
  final DateTime at;
  final bool forced;
}

/// The host as the ops console sees it: every process under a loop, and
/// the ones nothing owns any more. The loop reaps its own leaks at task
/// boundaries; the app is the catch for what a loop cannot see — a loop
/// that died mid-task, a probe whose browser outlived it hours ago. Every
/// scan kills the orphans it finds and writes them to
/// `<runs root>/host-leaks.jsonl`, so a widget that keeps things open is
/// a number on Home rather than lost RAM.
class HostWatch extends ChangeNotifier {
  HostWatch({required Directory runsRoot, Duration poll = const Duration(seconds: 10)}) {
    _runsRoot = runsRoot;
    if (Platform.isLinux) {
      scan();
      _timer = Timer.periodic(poll, (_) => scan());
    }
  }

  static const mark = 'MIZPAH_LOOP';

  late Directory _runsRoot;
  Timer? _timer;
  bool _scanning = false;

  /// Everything under a loop, as of the last scan.
  List<HostProcess> marked = const [];

  /// Leaks caught this session, newest first (last 50 kept).
  List<Reaped> reaped = const [];
  int reapedTotal = 0;
  int reapedMb = 0;
  DateTime? lastScan;
  String? error;

  int get loops => marked.where((p) => p.isLoop).length;
  int get rssMb => marked.fold(0, (s, p) => s + p.rssMb);

  /// Reaped, by command name: "chromium ×3, python3 ×1".
  String get reapedByComm {
    final counts = <String, int>{};
    for (final r in reaped) {
      counts[r.process.comm] = (counts[r.process.comm] ?? 0) + 1;
    }
    final keys = counts.keys.toList()..sort();
    return keys.map((k) => '$k ×${counts[k]}').join(', ');
  }

  void setRunsRoot(Directory root) {
    _runsRoot = root;
  }

  void setPoll(Duration poll) {
    _timer?.cancel();
    if (Platform.isLinux) _timer = Timer.periodic(poll, (_) => scan());
  }

  Future<void> scan() async {
    if (_scanning || !Platform.isLinux) return;
    _scanning = true;
    try {
      final found = await _list();
      final orphans = found.where((p) => p.orphaned).toList();
      final caught = <Reaped>[];
      for (final p in orphans) {
        Process.killPid(p.pid, ProcessSignal.sigterm);
      }
      if (orphans.isNotEmpty) {
        await Future<void>.delayed(const Duration(seconds: 2));
        for (final p in orphans) {
          var forced = false;
          if (Directory('/proc/${p.pid}').existsSync()) {
            Process.killPid(p.pid, ProcessSignal.sigkill);
            forced = true;
          }
          caught.add(Reaped(p, DateTime.now(), forced: forced));
        }
        await _record(caught);
      }
      marked = found.where((p) => !p.orphaned).toList();
      if (caught.isNotEmpty) {
        reaped = [...caught.reversed, ...reaped].take(50).toList();
        reapedTotal += caught.length;
        reapedMb += caught.fold(0, (s, r) => s + r.process.rssMb);
      }
      lastScan = DateTime.now();
      error = null;
    } catch (e) {
      error = '$e';
    } finally {
      _scanning = false;
      notifyListeners();
    }
  }

  Future<void> _record(List<Reaped> caught) async {
    try {
      final sink = File('${_runsRoot.path}/host-leaks.jsonl').openWrite(mode: FileMode.append);
      for (final r in caught) {
        sink.writeln(jsonEncode({
          'at': r.at.toIso8601String(),
          'forced': r.forced,
          ...r.process.toJson(),
        }));
      }
      await sink.close();
    } catch (_) {
      // The kill already happened; a log that cannot be written is not a
      // reason to stop catching.
    }
  }

  Future<List<HostProcess>> _list() async {
    final me = pid;
    final out = <HostProcess>[];
    double uptime = 0;
    try {
      uptime = double.parse(File('/proc/uptime').readAsStringSync().split(' ').first);
    } catch (_) {}
    for (final entry in Directory('/proc').listSync(followLinks: false)) {
      final name = entry.path.split('/').last;
      final id = int.tryParse(name);
      if (id == null || id == me) continue;
      final p = _read(id, uptime);
      if (p != null) out.add(p);
    }
    return out;
  }

  HostProcess? _read(int id, double uptime) {
    final base = '/proc/$id';
    try {
      final environ = File('$base/environ').readAsBytesSync();
      final key = utf8.encode('$mark=');
      final root = _envValue(environ, key);
      if (root == null) return null;
      final stat = File('$base/stat').readAsStringSync();
      final close = stat.lastIndexOf(')');
      final comm = stat.substring(stat.indexOf('(') + 1, close);
      final fields = stat.substring(close + 2).split(' ');
      final ppid = int.parse(fields[1]);
      // Field 22 of stat is start time in clock ticks; 100 Hz on every
      // Linux this app runs on.
      final started = int.tryParse(fields[19]) ?? 0;
      final age = uptime > 0 ? (uptime - started / 100).round() : 0;
      var rssKb = 0;
      for (final line in File('$base/status').readAsLinesSync()) {
        if (line.startsWith('VmRSS:')) {
          rssKb = int.tryParse(line.split(RegExp(r'\s+'))[1]) ?? 0;
          break;
        }
      }
      final cmdline = utf8
          .decode(File('$base/cmdline').readAsBytesSync(), allowMalformed: true)
          .replaceAll('\u0000', ' ')
          .trim();
      return HostProcess(
        pid: id,
        ppid: ppid,
        comm: comm,
        cmdline: cmdline.length > 200 ? cmdline.substring(0, 200) : cmdline,
        root: root,
        rssMb: rssKb ~/ 1024,
        ageSeconds: age < 0 ? 0 : age,
      );
    } catch (_) {
      // Gone between listing and reading, or not ours to read.
      return null;
    }
  }

  String? _envValue(List<int> environ, List<int> key) {
    var start = 0;
    for (var i = 0; i <= environ.length; i++) {
      if (i == environ.length || environ[i] == 0) {
        if (i - start >= key.length) {
          var match = true;
          for (var k = 0; k < key.length; k++) {
            if (environ[start + k] != key[k]) {
              match = false;
              break;
            }
          }
          if (match) {
            return utf8.decode(environ.sublist(start + key.length, i), allowMalformed: true);
          }
        }
        start = i + 1;
      }
    }
    return null;
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}
