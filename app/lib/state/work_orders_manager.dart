import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/document.dart';
import 'brief_manager.dart';

/// The selected project's work-order folder: one sheet per route task.
/// Follows [BriefManager]'s selection; re-reads on the engine's tick.
class WorkOrdersManager extends ChangeNotifier {
  WorkOrdersManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _changes = _engine.changes.listen((_) => reload(quiet: true));
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  String? _loaded;

  List<InboxDocument> orders = const [];
  bool loading = false;
  String? openId;

  InboxDocument? get opened =>
      orders.where((d) => d.number == openId).firstOrNull;

  void select(InboxDocument d) {
    openId = d.number;
    notifyListeners();
  }

  Future<void> reload({bool quiet = false}) async {
    final id = _briefs.selectedId;
    if (id == null) {
      orders = const [];
      notifyListeners();
      return;
    }
    if (!quiet) {
      loading = true;
      notifyListeners();
    }
    final docs = await _engine.readWorkOrders(id);
    if (_briefs.selectedId != id) return;
    orders = docs;
    loading = false;
    if (openId == null || opened == null) {
      // Open what is moving, else the first thing issued.
      openId = (orders.where((d) => d.status == 'IN PROGRESS').firstOrNull ??
              orders.firstOrNull)
          ?.number;
    }
    notifyListeners();
  }

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _loaded) return;
    _loaded = id;
    openId = null;
    orders = const [];
    reload();
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _changes?.cancel();
    super.dispose();
  }
}
