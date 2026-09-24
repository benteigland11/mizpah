import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/route.dart';
import 'brief_manager.dart';

/// The selected project's route: status, log, a scope (phase or enabler)
/// and the selected task. Follows [BriefManager]'s selection and reloads
/// whenever the brief reloads (a save may have changed phases or budget).
class RouteManager extends ChangeNotifier {
  RouteManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    // The route moves under the loop: a task picked up, finished, blocked.
    _changes = _engine.changes.listen((_) => reload());
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  String? _watching;
  int _generation = -1;

  RouteStatus status = RouteStatus.empty;
  List<RouteEvent> log = const [];

  /// Scope: only this phase / enabler. Null = all.
  String? phase;
  String? enabler;
  String? selectedTask;
  String? error;

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _watching && _briefs.generation == _generation) return;
    if (id != _watching) {
      phase = null;
      enabler = null;
      selectedTask = null;
    }
    _watching = id;
    _generation = _briefs.generation;
    reload();
  }

  Future<void> reload() async {
    final id = _watching;
    if (id == null) {
      status = RouteStatus.empty;
      log = const [];
      notifyListeners();
      return;
    }
    try {
      status = await _engine.readRoute(id);
      log = await _engine.readRouteLog(id);
      error = null;
    } catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  List<RouteTask> get scoped => [
    for (final t in status.tasks)
      if ((phase == null || t.phase == phase) &&
          (enabler == null || t.enablerId == enabler))
        t,
  ];

  RouteTask? get selected =>
      selectedTask == null ? null : status.task(selectedTask!);

  List<RouteEvent> eventsFor(String taskId) =>
      log.where((e) => e.task == taskId).toList();

  void scopeToPhase(String? id) {
    phase = id;
    enabler = null;
    notifyListeners();
  }

  void scopeToEnabler(String? id) {
    enabler = id;
    phase = null;
    notifyListeners();
  }

  void select(String? taskId) {
    selectedTask = taskId;
    notifyListeners();
  }

  Future<void> _write(Future<void> Function(String id) f) async {
    final id = _watching;
    if (id == null) return;
    try {
      await f(id);
      error = null;
    } catch (e) {
      error = '$e';
    }
    await reload();
  }

  Future<void> setPriority(String taskId, String p, String reason) =>
      _write((id) => _engine.setPriority(id, taskId, p, reason));

  Future<void> cancel(String taskId, String reason) =>
      _write((id) => _engine.cancelTask(id, taskId, reason));

  Future<void> unblock(String taskId) =>
      _write((id) => _engine.unblockTask(id, taskId));

  @override
  void dispose() {
    _changes?.cancel();
    _briefs.removeListener(_follow);
    super.dispose();
  }
}
