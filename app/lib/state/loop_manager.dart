import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/loop.dart';
import 'brief_manager.dart';

/// The selected project's loop, live. Follows [BriefManager]'s selection.
class LoopManager extends ChangeNotifier {
  LoopManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<LoopState>? _sub;
  String? _watching;

  LoopState state = LoopState.idle;

  /// Which session's transcript is open: a task id, or 'controller'.
  String selected = 'controller';
  List<Turn> transcript = const [];
  StreamSubscription<List<Turn>>? _tsub;

  void select(String session) {
    if (session == selected && _tsub != null) return;
    selected = session;
    transcript = const [];
    _tsub?.cancel();
    final id = _watching;
    if (id != null) {
      _tsub = _engine.watchTranscript(id, session).listen((t) {
        transcript = t;
        notifyListeners();
      });
    }
    notifyListeners();
  }

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _watching) return;
    _watching = id;
    _sub?.cancel();
    _tsub?.cancel();
    _tsub = null;
    state = LoopState.idle;
    transcript = const [];
    if (id != null) {
      _sub = _engine.watchLoop(id).listen((s) {
        state = s;
        notifyListeners();
      });
      select(selected);
    }
    notifyListeners();
  }

  Future<void> setMode(LoopMode mode) async {
    final id = _watching;
    if (id == null) return;
    await _engine.setMode(id, mode);
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _sub?.cancel();
    _tsub?.cancel();
    super.dispose();
  }
}
