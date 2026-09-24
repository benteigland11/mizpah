import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../engine/run_floor.dart';
import '../models/loop.dart';
import 'brief_manager.dart';

/// The agents' traces. In a task: that task's controller and workers. At
/// Home (no task open): every live task, each with the worker currently
/// on it. One transcript is watched at a time — the one in view.
class AgentsManager extends ChangeNotifier {
  AgentsManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _changes = _engine.changes.listen((_) => refreshSeats());
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  StreamSubscription<List<Turn>>? _watch;
  String? _scope; // the task id whose seats are listed, or null for Home

  /// Seats to pick from, each tagged with its task.
  List<(BriefSummary, FloorSession)> seats = const [];
  (String, String)? selected; // (task id, session id)

  /// The live tail, re-read while watched.
  List<Turn> _tail = const [];
  int _tailStart = 0;

  /// Older slices the person scrolled back for, oldest first. The log is
  /// append-only so these never change once read.
  final List<TraceWindow> _earlier = [];
  bool loadingEarlier = false;
  bool atBeginning = false;

  List<Turn> get transcript => [for (final w in _earlier) ...w.turns, ..._tail];

  /// The rules in the part of the log before the first tail read: read once
  /// per seat, so the trace's rule menu lists the whole session.
  List<Turn> earlierRules = const [];

  /// Page back until the rule with [key] is in the transcript (or the log
  /// begins). True when it is there to scroll to.
  Future<bool> reachRule(String key) async {
    final seat = selected;
    while (selected == seat && !transcript.any((t) => t.key == key)) {
      if (atBeginning) return false;
      if (loadingEarlier) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
        continue;
      }
      await loadEarlier();
    }
    return selected == seat;
  }
  bool loading = true;

  /// `{controller, worker}` model names per task id, for the trace header.
  final Map<String, Map<String, String>> crews = {};

  /// The model behind the selected seat.
  String get selectedModel {
    final key = selected;
    if (key == null) return '';
    final c = crews[key.$1];
    if (c == null) return '';
    return (key.$2.startsWith('controller') ? c['controller'] : c['worker']) ?? '';
  }

  /// Page back one window from the oldest we hold. The controller's seat
  /// is one journal; it has nothing earlier.
  Future<void> loadEarlier() async {
    final key = selected;
    if (key == null || key.$2.startsWith('controller') || loadingEarlier || atBeginning) return;
    final end = _earlier.isEmpty ? _tailStart : _earlier.first.start;
    if (end <= 0) {
      atBeginning = true;
      notifyListeners();
      return;
    }
    loadingEarlier = true;
    notifyListeners();
    final w = await _engine.readTraceWindow(key.$1, key.$2, end: end);
    if (selected == key) {
      _earlier.insert(0, w);
      atBeginning = w.atBeginning;
    }
    loadingEarlier = false;
    notifyListeners();
  }

  bool get homeWide => _briefs.selectedId == null;

  Future<void> refreshSeats() async {
    final id = _briefs.selectedId;
    final out = <(BriefSummary, FloorSession)>[];
    if (id != null) {
      final me = _briefs.briefs.where((b) => b.id == id).firstOrNull;
      if (me != null) {
        for (final s in await _engine.readSessions(id)) {
          out.add((me, s));
        }
      }
    }
    // No task open: nothing to watch; the Agents tab lives in the task.
    seats = out;
    for (final id in {for (final e in out) e.$1.id}) {
      crews[id] = await _engine.readCrew(id);
    }
    loading = false;
    // Keep the seat we were watching; else the busiest one; else the first.
    final still = selected != null &&
        seats.any((e) => e.$1.id == selected!.$1 && e.$2.id == selected!.$2);
    if (!still) {
      final busy = seats.where((e) => e.$2.status == 'in_progress').firstOrNull ?? seats.firstOrNull;
      _select(busy == null ? null : (busy.$1.id, busy.$2.id));
    }
    notifyListeners();
  }

  void select(BriefSummary task, FloorSession seat) => _select((task.id, seat.id));

  /// Whether anyone is looking. The page stays alive behind other tabs
  /// (an IndexedStack), and a tail poll nobody watches is pure cost:
  /// off-screen the seat is remembered and the poll stops; back on
  /// screen it picks up from the tail.
  bool _watching = true;
  void setWatching(bool on) {
    if (on == _watching) return;
    _watching = on;
    final key = selected;
    if (!on) {
      _watch?.cancel();
      _watch = null;
      _poll?.cancel();
      _poll = null;
    } else if (key != null) {
      _select(key);
    }
  }

  void _select((String, String)? key) {
    if (key == selected && (_watch != null || _poll != null)) return;
    selected = key;
    _tail = const [];
    _tailStart = 0;
    // The signature goes with the tail: kept, a seat left and returned to
    // while the log stood still (a worker mid-reasoning for two minutes)
    // matched its own old signature and showed "Nothing yet" until the
    // log grew.
    _tailSig = '';
    wire = null;
    reading = key != null && !key.$2.startsWith('controller');
    _earlier.clear();
    earlierRules = const [];
    atBeginning = key != null && key.$2.startsWith('controller');
    _watch?.cancel();
    _watch = null;
    _poll?.cancel();
    _poll = null;
    if (key != null && _watching) {
      if (key.$2.startsWith('controller')) {
        _watch = _engine.watchTranscript(key.$1, key.$2).listen((t) {
          _tail = t;
          notifyListeners();
        });
      } else {
        // The tail is anchored where it was first read and grows with the
        // file, so pages loaded behind it never drift out of contact.
        int? anchor;
        int? seenLength;
        Future<void> tick() async {
          // Say what we hold: a log that has not grown costs a stat, not a read.
          final w = await _engine.readTraceWindow(key.$1, key.$2, from: anchor, ifLength: seenLength);
          if (selected != key) return;
          if (reading) {
            reading = false;
            notifyListeners();
          }
          if (w.wire?.at != wire?.at) {
            wire = w.wire;
            notifyListeners();
          }
          if (w.unchanged) return;
          seenLength = w.fileLength;
          if (anchor == null) {
            anchor = w.start;
            _tailStart = w.start;
            atBeginning = w.atBeginning;
            if (w.start > 0) {
              unawaited(_engine.readTraceRules(key.$1, key.$2, before: w.start).then((r) {
                if (selected != key) return;
                earlierRules = r;
                notifyListeners();
              }).catchError((_) {}));
            }
          }
          final sig = '${w.turns.length}:${w.end}';
          if (sig != _tailSig) {
            _tailSig = sig;
            _tail = w.turns;
            notifyListeners();
          }
        }
        tick();
        _poll = Timer.periodic(const Duration(seconds: 2), (_) => tick());
      }
    }
    notifyListeners();
  }

  Timer? _poll;
  String _tailSig = '';

  /// The first read of a seat's tail is out: the log's last 24 MB is being
  /// scanned, and an empty trace meanwhile is not "nothing yet".
  bool reading = false;

  /// The reply on the wire for the selected worker: when its last byte
  /// came. Fresh every tick; the live row's clock runs against it.
  Wire? wire;

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _scope && seats.isNotEmpty) return;
    _scope = id;
    selected = null;
    seats = const [];
    loading = true;
    notifyListeners();
    refreshSeats();
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _changes?.cancel();
    _watch?.cancel();
    _poll?.cancel();
    super.dispose();
  }
}
