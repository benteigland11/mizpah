import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/databook.dart';
import 'brief_manager.dart';

/// The selected project's data book. Follows [BriefManager]'s selection;
/// re-reads when the engine reports the project moved.
class DataBookManager extends ChangeNotifier {
  DataBookManager(this._engine, this._briefs) {
    _briefs.addListener(_follow);
    _changes = _engine.changes.listen((_) => reload());
    _follow();
  }

  final Engine _engine;
  final BriefManager _briefs;
  StreamSubscription<void>? _changes;
  String? _loaded;

  DataBook book = DataBook.empty;
  bool loading = false;

  /// Entry (kind, index) whose report is unfolded.
  (String, int)? open;

  /// brief | map
  String view = 'graph';

  /// The node selected in the threads view (`need:3`, `k:<id>`, `m:<map>`).
  String? selectedNode;

  void selectNode(String? id) {
    selectedNode = id;
    notifyListeners();
  }

  /// The brief entry behind the selection: the entry itself, or the entry a
  /// selected reading answers.
  DataBookEntry? selectedEntry(DataBook book) {
    final id = selectedNode;
    if (id == null) return null;
    for (final e in book.entries) {
      if ('${e.kind}:${e.index}' == id) return e;
      if (e.answers.any((k) => 'k:${k.id}' == id) || e.open.any((u) => 'u:${u.id}' == id)) return e;
    }
    return null;
  }

  void setView(String v) {
    if (v == view) return;
    view = v;
    notifyListeners();
  }

  void toggle(DataBookEntry e) {
    final key = (e.kind, e.index);
    open = open == key ? null : key;
    notifyListeners();
  }

  Future<void> reload() async {
    final id = _briefs.selectedId;
    if (id == null) {
      book = DataBook.empty;
      notifyListeners();
      return;
    }
    final b = await _engine.readDataBook(id);
    if (_briefs.selectedId != id) return;
    book = b;
    loading = false;
    notifyListeners();
  }

  void _follow() {
    final id = _briefs.selectedId;
    if (id == _loaded) return;
    _loaded = id;
    open = null;
    book = DataBook.empty;
    loading = true;
    notifyListeners();
    reload();
  }

  @override
  void dispose() {
    _briefs.removeListener(_follow);
    _changes?.cancel();
    super.dispose();
  }
}
