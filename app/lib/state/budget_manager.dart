import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/budget.dart';
import 'brief_manager.dart';

/// The selected project's ledger. Follows [BriefManager]'s selection and
/// re-reads on the engine's change tick and on every brief save (the
/// budget lives on the brief).
class BudgetManager extends ChangeNotifier {
  BudgetManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _changes = _engine.changes.listen((_) => reload());
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  String? _loaded;
  int _generation = -1;

  BudgetLedger ledger = BudgetLedger.empty;
  bool loading = false;

  Future<void> reload() async {
    final id = _briefs.selectedId;
    if (id == null) {
      ledger = BudgetLedger.empty;
      notifyListeners();
      return;
    }
    final l = await _engine.readBudget(id);
    if (_briefs.selectedId != id) return;
    ledger = l;
    loading = false;
    notifyListeners();
  }

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _loaded && _briefs.generation == _generation) return;
    final switched = id != _loaded;
    _loaded = id;
    _generation = _briefs.generation;
    if (switched) {
      ledger = BudgetLedger.empty;
      loading = true;
      notifyListeners();
    }
    reload();
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _changes?.cancel();
    super.dispose();
  }
}
