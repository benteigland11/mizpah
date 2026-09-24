import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../engine/run_floor.dart';
import '../models/document.dart';
import '../models/loop.dart';
import 'brief_manager.dart';

/// The selected project's paperwork. Follows [BriefManager]'s selection;
/// holds which document is open.
class DailyWorkManager extends ChangeNotifier {
  DailyWorkManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _changes = _engine.changes.listen((_) => reload(quiet: true));
    _follow();
    // The bar's quick view of what the run is doing, polled while it is live: the host's steps and a seat's
    // phase move faster than the change stream fires.
    _nowTick = Timer.periodic(const Duration(seconds: 3), (_) => _refreshNow());
  }

  Timer? _nowTick;

  /// What the live run is doing right now, in one line for the project bar:
  /// `WORKER · repair_score_following_video · gate red`, `HOST · re-taking
  /// stale reading 2 of 5: …`, `CONTROLLER · calling the model`. Empty when
  /// nothing is live.
  String now = '';

  Future<void> _refreshNow() async {
    final id = _briefs.selectedId;
    if (holding && !anyLive) {
      holding = false;
      now = '';
      notifyListeners();
      return;
    }
    if (holding) return;
    var next = '';
    if (id != null && anyLive) {
      try {
        next = describeNow(await _engine.readSessions(id));
      } catch (_) {
        next = now;
      }
    }
    if (_briefs.selectedId != id || next == now) return;
    now = next;
    notifyListeners();
  }

  /// One line for the live seat: the host between seats, a decision being
  /// made, or the worker the loop is on (not one waiting its turn).
  static String describeNow(List<FloorSession> seats) {
    final host = seats.where((s) => s.isHost).firstOrNull;
    if (host != null) return 'HOST · ${host.title}';
    final deciding = seats.where((s) => s.isController && s.phase.isNotEmpty).firstOrNull;
    if (deciding != null) return 'CONTROLLER · ${deciding.phase}';
    final working = seats.where((s) => s.status == 'in_progress' && !s.phase.startsWith('waiting')).toList()
      ..sort((a, b) => (b.lastActiveAt ?? DateTime(0)).compareTo(a.lastActiveAt ?? DateTime(0)));
    if (working.isEmpty) return '';
    final w = working.first;
    return 'WORKER · ${w.id}${w.phase.isEmpty ? '' : ' · ${w.phase}'}';
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  String? _loaded;
  String _sessionsShown = '';

  /// Arrival order, oldest first.
  List<InboxDocument> documents = const [];

  /// The open sheet, by what it is: a re-read builds new documents, and an
  /// index or an identity then points at nothing (`indexOf` gave -1, and
  /// the desk threw building) or at whatever took its place.
  String? _openKey;
  String? get openKey => _openKey;

  static String _keyOf(InboxDocument d) =>
      '${d.kind.name}:${d.number.isNotEmpty ? d.number : '${d.at?.toUtc().millisecondsSinceEpoch ?? 0}|${d.title}'}';
  bool loading = false;

  /// `{controller, worker}` labels for the open task.
  Map<String, String> crew = const {};

  /// The project's sessions, newest first, and the one the desk shows.
  List<RunSession> get sessions {
    final id = _briefs.selectedId;
    return id == null ? const [] : _engine.sessions(id);
  }

  String? get session {
    final id = _briefs.selectedId;
    return id == null ? null : _engine.currentSession(id);
  }

  /// The last action that failed, in one line, or null. A remote call that
  /// the desktop app does not know yet (an older build) used to fail silently.
  String? problem;

  /// What the desk is doing right now on the person's behalf ("Archiving",
  /// "Resuming"), while it is: the screen holds the desk under a notice.
  /// Over the wire these take seconds; nothing should look ignored.
  String? working;

  /// Put the shown session away, or remove it. After either the desk shows
  /// whatever session the project has left (the engine picks).
  Future<void> archiveSession(String path) => _act('Archive', () => _engine.archiveSession(_briefs.selectedId!, path));

  Future<void> deleteSession(String path) => _act('Delete', () => _engine.deleteSession(_briefs.selectedId!, path));

  Future<void> _act(String verb, Future<void> Function() action) async {
    if (_briefs.selectedId == null) return;
    _openKey = null;
    problem = null;
    working = verb;
    notifyListeners();
    try {
      await action();
    } catch (e) {
      problem = '$verb failed: $e';
      return;
    } finally {
      working = null;
      notifyListeners();
    }
    await reload();
  }

  /// Whether the shown session's loop is running now.
  bool get running => sessions.where((s) => s.path == session).firstOrNull?.running ?? false;

  /// Whether any session of the project is live, whichever is shown: what
  /// HOLD acts on, and what makes RESUME wrong.
  bool get anyLive => sessions.any((s) => s.running);

  /// A note to the run. On a stopped run it also relaunches the loop, so
  /// the controller answers now rather than whenever it next runs.
  /// A memo to the worker on a live task, delivered at its next boundary.
  Future<void> memoToWorker(String task, String text) async {
    final id = _briefs.selectedId;
    if (id == null) return;
    working = 'Sending';
    notifyListeners();
    try {
      await _engine.memoToWorker(id, task, text);
    } finally {
      working = null;
      notifyListeners();
    }
    await reload(quiet: true);
  }

  Future<void> reply(String text) async {
    final id = _briefs.selectedId;
    if (id == null) return;
    working = running ? 'Sending' : 'Sending and resuming';
    notifyListeners();
    try {
      await _engine.replyToRun(id, text, resume: !running);
    } finally {
      working = null;
      notifyListeners();
    }
    await reload(quiet: true);
  }

  /// Relaunch a stopped loop on the shown session; hold a running one at
  /// its next boundary.
  Future<void> resume() => _dial('Resuming', LoopMode.run);
  Future<void> hold() async {
    await _dial('Holding', LoopMode.hold);
    // The STOP lands at the next boundary — a worker's turn, a reading, the start of a review — not at the
    // click: until the run is no longer live the bar says it is holding, so the click never looks ignored.
    if (problem == null && anyLive) {
      holding = true;
      now = 'HOLDING · STOPS AT THE NEXT BOUNDARY';
      notifyListeners();
    }
  }

  /// HOLD was pressed and the run has not stopped yet.
  bool holding = false;

  Future<void> _dial(String verb, LoopMode mode) async {
    final id = _briefs.selectedId;
    if (id == null) return;
    working = verb;
    problem = null;
    notifyListeners();
    try {
      await _engine.setMode(id, mode);
      // The launch returns once the process is spawned; the run shows live
      // only when the loop has written its record, seconds later. Keep the
      // notice up until then, so the click never looks ignored.
      if (mode == LoopMode.run) await _untilLive();
    } catch (e) {
      problem = '$verb failed: $e';
    } finally {
      working = null;
      notifyListeners();
    }
  }

  Future<void> _untilLive() async {
    final deadline = DateTime.now().add(const Duration(seconds: 45));
    while (!anyLive) {
      if (DateTime.now().isAfter(deadline)) {
        problem = 'Resume sent, but the loop has not shown live after 45s';
        return;
      }
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
  }

  /// Show another session's paperwork. The engine rescans and its change
  /// stream brings the desk back here with the other session's documents.
  void chooseSession(String path) {
    final id = _briefs.selectedId;
    if (id == null) return;
    _openKey = null;
    _engine.chooseSession(id, path);
    reload();
  }

  /// Newest first: what the desk shows.
  List<InboxDocument> get desk => documents.reversed.toList();

  /// The desk shows this many of the newest documents; SHOW EARLIER adds a
  /// page. Keeps a long run's list light on a tablet, where a canvas that
  /// grows without bound stops painting.
  static const page = 40;
  int shown = page;
  void showMore() {
    shown += page;
    notifyListeners();
  }

  InboxDocument? get opened =>
      _openKey == null ? null : documents.where((d) => _keyOf(d) == _openKey).firstOrNull;

  int get awaitingSignature =>
      documents.where((d) => d.awaitingSignature).length;

  /// Open the document a line links to (`<kind>:<number>`), if the desk holds it.
  void openLink(String link) {
    final sep = link.indexOf(':');
    if (sep < 0) return;
    final kind = link.substring(0, sep), number = link.substring(sep + 1);
    final target = documents.where((d) => d.kind.name == kind && d.number == number).firstOrNull;
    if (target != null) select(target);
  }

  bool isOpen(InboxDocument d) => _openKey != null && _keyOf(d) == _openKey;

  void select(InboxDocument d) {
    _openKey = _keyOf(d);
    notifyListeners();
  }

  /// The next or previous sheet on the desk (as listed, newest first),
  /// for the keyboard.
  void step(int by) {
    final list = desk;
    if (list.isEmpty) return;
    final at = _openKey == null ? -1 : list.indexWhere((d) => _keyOf(d) == _openKey);
    final to = (at < 0 ? (by > 0 ? 0 : list.length - 1) : at + by).clamp(0, list.length - 1);
    select(list[to]);
  }

  /// Sign a change request from the desk. Goes to the engine for the open
  /// task; the brief manager re-reads so its draft matches the disk.
  Future<void> decide(InboxDocument d, {required bool accept, String reason = ''}) async {
    final id = _briefs.selectedId;
    final pid = d.proposalId;
    if (id == null || pid == null) return;
    if (accept) {
      await _engine.acceptProposal(id, pid, reason: reason);
    } else {
      await _engine.rejectProposal(id, pid, reason: reason);
    }
    await _briefs.select(id);
    await reload(quiet: true);
  }

  /// [quiet] keeps the desk in place while new paper is fetched — for a
  /// live run, where documents arrive under the reader.
  Future<void> reload({bool quiet = false}) async {
    final id = _briefs.selectedId;
    if (id == null) {
      documents = const [];
      _openKey = null;
      notifyListeners();
      return;
    }
    if (!quiet) {
      loading = true;
      notifyListeners();
    }
    final docs = await _engine.readInbox(id);
    if (_briefs.selectedId != id) return; // selection moved on meanwhile
    crew = await _engine.readCrew(id);
    // Same paper, same stamps: nothing to repaint. A signed change request
    // is the same count with a new stamp, so compare stamps, not length.
    // A running work order's header (turns so far, tokens) moves too.
    String sig(List<InboxDocument> ds) => ds
        .map((d) => '${d.kind.name}:${d.number}:${d.status}:${d.header.map((h) => h.$2).join(',')}')
        .join('|');
    // The session line is part of the desk too: a remote learns the
    // sessions a beat after the paper, and that must repaint.
    final sessionsNow = '${sessions.map((s) => '${s.path}:${s.running}:${s.archived}').join('|')}#$session';
    if (quiet && sig(docs) == sig(documents) && sessionsNow == _sessionsShown) return;
    _sessionsShown = sessionsNow;
    documents = docs;
    loading = false;
    // The sheet you had open stays open; else the newest.
    if (opened == null) _openKey = documents.isEmpty ? null : _keyOf(documents.last);
    notifyListeners();
  }

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _loaded) return;
    _loaded = id;
    holding = false;
    now = '';
    _openKey = null;
    shown = page;
    documents = const []; // the old project's paper leaves the desk first
    reload();
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _changes?.cancel();
    _nowTick?.cancel();
    super.dispose();
  }
}
