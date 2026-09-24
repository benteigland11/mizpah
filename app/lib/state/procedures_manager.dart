import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../engine/procedure_history.dart';
import '../widgets/facet_filter.dart';
import '../models/procedure.dart';

/// The playbook, read for people: every procedure, who minted it, who has
/// used it; one open for reading and editing. Edits go to the playbook
/// CLI and the list re-reads on the engine's tick.
class ProceduresManager extends ChangeNotifier {
  ProceduresManager(this._engine) {
    _changes = _engine.changes.listen((_) => reload());
    reload();
  }

  final Engine _engine;
  StreamSubscription<void>? _changes;

  List<Procedure> all = const [];
  bool loading = true;

  /// Use it or lose it: the ledger's rules and clock, read with the list.
  LibraryPolicy policy = const LibraryPolicy();

  /// The decayed drawer on the list is folded until opened by hand.
  bool showRetired = false;
  String query = '';
  String? openId_;
  String? get openId => openId_;
  String? error;

  /// An id the list should scroll into view once, then forget.
  String? reveal;

  /// Column filters (a funnel beside +). Empty selection = all.
  Map<String, Set<String>> picked = {'task': {}, 'tag': {}, 'use': {}};
  bool get filtered => query.trim().isNotEmpty || picked.values.any((s) => s.isNotEmpty);

  static String useOf(Procedure p) => p.usedBy.isEmpty ? 'unused' : 'used';
  static String taskOf(Procedure p) => p.mintedBy?.taskTitle ?? '(not from a run here)';

  List<Facet> get facets {
    final task = <String, int>{}, tag = <String, int>{}, use = <String, int>{};
    for (final p in all) {
      task[taskOf(p)] = (task[taskOf(p)] ?? 0) + 1;
      use[useOf(p)] = (use[useOf(p)] ?? 0) + 1;
      for (final t in p.tags) {
        tag[t] = (tag[t] ?? 0) + 1;
      }
    }
    return [
      Facet(key: 'task', label: 'Minted by task', values: task),
      Facet(key: 'tag', label: 'Tag', values: tag),
      Facet(key: 'use', label: 'Used', values: use),
    ];
  }

  void setPicked(Map<String, Set<String>> v) {
    picked = v;
    notifyListeners();
  }

  void clearFilters() {
    for (final s in picked.values) {
      s.clear();
    }
    query = '';
    notifyListeners();
  }

  /// Filtered and ordered: minted by a run on this machine first (newest
  /// mint on top), then the rest by last change. Retired procedures are
  /// not here; they are [retiredShown].
  List<Procedure> get shown => _filtered(all.where((p) => !p.retired));

  /// Retired procedures under the same filters, most recently retired first.
  List<Procedure> get retiredShown {
    final list = _filtered(all.where((p) => p.retired));
    list.sort((a, b) => (b.standing.retiredAt ?? 0).compareTo(a.standing.retiredAt ?? 0));
    return list;
  }

  int get retiredCount => all.where((p) => p.retired).length;

  List<Procedure> _filtered(Iterable<Procedure> from) {
    final q = query.trim().toLowerCase();
    final list = from.where((p) {
      if (picked['task']!.isNotEmpty && !picked['task']!.contains(taskOf(p))) return false;
      if (picked['tag']!.isNotEmpty && !p.tags.any(picked['tag']!.contains)) return false;
      if (picked['use']!.isNotEmpty && !picked['use']!.contains(useOf(p))) return false;
      if (q.isEmpty) return true;
      return p.id.contains(q) ||
          p.title.toLowerCase().contains(q) ||
          p.tags.any((t) => t.toLowerCase().contains(q)) ||
          p.description.toLowerCase().contains(q);
    }).toList();
    list.sort((a, b) {
      final am = a.mintedBy != null, bm = b.mintedBy != null;
      if (am != bm) return am ? -1 : 1;
      return b.updatedAt.compareTo(a.updatedAt);
    });
    return list;
  }

  int get minted => all.where((p) => p.mintedBy != null).length;

  void search(String q) {
    query = q;
    notifyListeners();
  }

  void open(Procedure p) {
    if (p.id == openId_) return;
    openId_ = p.id;
    revert();
    loadHistory();
    // A person reading it is a touch, the weakest kind. Quiet on purpose.
    if (!p.retired) unawaited(_engine.touchProcedure(p.id).catchError((_) {}));
  }

  void toggleRetired() {
    showRetired = !showRetired;
    notifyListeners();
  }

  Future<void> _library(Future<void> Function() op) async {
    try {
      await op();
      error = null;
    } catch (e) {
      error = '$e';
      notifyListeners();
    }
  }

  Future<void> pin(String id, bool on) => _library(() => _engine.pinProcedure(id, on));
  Future<void> retire(String id) => _library(() => _engine.retireProcedure(id));
  Future<void> revive(String id) => _library(() => _engine.reviveProcedure(id));
  Future<void> setPolicy({bool? enabled, int? grace}) => _library(() => _engine.setLibraryPolicy(enabled: enabled, grace: grace));

  Future<void> reload() async {
    all = await _engine.readProcedures();
    policy = await _engine.readLibraryPolicy();
    loading = false;
    if (openId_ == null) {
      openId_ = shown.firstOrNull?.id;
      unawaited(loadHistory());
    }
    notifyListeners();
  }

  void openById(String id) {
    if (all.any((p) => p.id == id) && id != openId_) {
      openId_ = id;
      revert();
      loadHistory();
    }
  }

  /// Start a procedure of your own and open it for writing.
  Future<void> create(String title) async {
    error = null;
    try {
      final id = await _engine.createProcedure(title);
      await reload();
      openId_ = id;
      revert();
      reveal = id; // the list scrolls to it
      await loadHistory();
    } catch (e) {
      error = '$e';
      notifyListeners();
    }
  }

  /// Procedures whose steps run [id]: what makes it undeletable.
  List<Procedure> linkersOf(String id) =>
      all.where((p) => p.steps.any((s) => s.procedure == id)).toList();

  Future<void> delete(String id) async {
    error = null;
    try {
      await _engine.deleteProcedure(id);
      if (openId_ == id) {
        openId_ = null;
        revert();
      }
      await reload();
    } catch (e) {
      error = '$e';
      notifyListeners();
    }
  }

  // ---- Draft: a person's edits accumulate here and reach the store as one
  // save (one commit). The draft is the procedure as it will read; the ops
  // are the playbook calls that make it so, replayed in order on save.

  Procedure? _draft;
  final List<Future<void> Function()> _ops = [];
  final List<String> _summary = [];

  /// What the sheet shows: the draft when there is one, else the store.
  Procedure? get opened => _draft ?? all.where((p) => p.id == openId_).firstOrNull;
  bool get dirty => _ops.isNotEmpty;
  bool saving = false;

  Procedure get _d {
    final base = _draft ?? all.firstWhere((p) => p.id == openId_);
    return _draft = base;
  }

  void _edit(Procedure next, Future<void> Function() op, String what) {
    _draft = next;
    _ops.add(op);
    _summary.add(what);
    notifyListeners();
  }

  Procedure _copy(Procedure p, {String? title, String? description, List<String>? tags, List<ProcedureStep>? steps}) => Procedure(
        id: p.id,
        title: title ?? p.title,
        description: description ?? p.description,
        tags: tags ?? p.tags,
        steps: steps ?? p.steps,
        updatedAt: p.updatedAt,
        mintedBy: p.mintedBy,
        usedBy: p.usedBy,
      );

  void rename(String id, String title) =>
      _edit(_copy(_d, title: title), () => _engine.editProcedure(id, title: title), 'title');
  void describe(String id, String description) =>
      _edit(_copy(_d, description: description), () => _engine.editProcedure(id, description: description), 'description');
  void retag(String id, List<String> tags) =>
      _edit(_copy(_d, tags: tags), () => _engine.editProcedure(id, tags: tags), 'tags');

  void editStep(String id, String stepTitle, {String? rename, String? do_}) {
    final steps = [
      for (final s in _d.steps)
        s.title == stepTitle ? ProcedureStep(id: s.id, title: rename ?? s.title, do_: do_ ?? s.do_, procedure: s.procedure) : s,
    ];
    _edit(_copy(_d, steps: steps), () => _engine.editStep(id, stepTitle, rename: rename, do_: do_), 'step "$stepTitle"');
  }

  void addStep(String id, String title, String do_, {String? after}) {
    final steps = [..._d.steps];
    final at = after == null ? steps.length : steps.indexWhere((s) => s.title == after) + 1;
    steps.insert(at < 0 ? steps.length : at, ProcedureStep(id: 'draft-${steps.length}', title: title, do_: do_));
    _edit(_copy(_d, steps: steps), () => _engine.addStep(id, title, do_, after: after), 'add step "$title"');
  }

  void removeStep(String id, String stepTitle) {
    final steps = _d.steps.where((s) => s.title != stepTitle).toList();
    _edit(_copy(_d, steps: steps), () => _engine.removeStep(id, stepTitle), 'remove step "$stepTitle"');
  }

  void moveStep(String id, String stepTitle, int to) {
    final steps = [..._d.steps];
    final i = steps.indexWhere((s) => s.title == stepTitle);
    if (i < 0) return;
    final s = steps.removeAt(i);
    steps.insert((to - 1).clamp(0, steps.length), s);
    _edit(_copy(_d, steps: steps), () => _engine.moveStep(id, stepTitle, to), 'move step "$stepTitle"');
  }

  void linkStep(String id, String stepTitle, String? procedure) {
    final steps = [
      for (final s in _d.steps)
        s.title == stepTitle ? ProcedureStep(id: s.id, title: s.title, do_: s.do_, procedure: procedure) : s,
    ];
    _edit(_copy(_d, steps: steps), () => _engine.linkStep(id, stepTitle, procedure ?? ''),
        procedure == null ? 'unlink step "$stepTitle"' : 'link step "$stepTitle"');
  }

  /// Replay the draft's ops against the store, then seal them as one
  /// commit. On a failure the ops that did not run stay pending.
  Future<void> save() async {
    final id = openId_;
    if (id == null || _ops.isEmpty || saving) return;
    saving = true;
    error = null;
    notifyListeners();
    try {
      while (_ops.isNotEmpty) {
        await _ops.first();
        _ops.removeAt(0);
      }
      final what = _summary.toSet().take(4).join(', ');
      _summary.clear();
      await _engine.commitProcedures('edit $id: $what');
      _draft = null;
      await reload();
      await loadHistory();
    } catch (e) {
      error = '$e';
    } finally {
      saving = false;
      notifyListeners();
    }
  }

  void revert() {
    _draft = null;
    _ops.clear();
    _summary.clear();
    error = null;
    notifyListeners();
  }

  // ---- History: the store is a git repository; each version is a commit.

  List<ProcedureVersion> history = const [];
  ProcedureVersion? viewing;
  List<DiffHunkLine> diff = const [];

  Future<void> loadHistory() async {
    final id = openId_;
    if (id == null) return;
    history = await _engine.procedureLog(id);
    viewing = null;
    diff = const [];
    notifyListeners();
  }

  Future<void> view(ProcedureVersion v) async {
    final id = openId_;
    if (id == null) return;
    viewing = v;
    diff = await _engine.procedureDiff(id, v.sha);
    notifyListeners();
  }

  Future<void> restore(ProcedureVersion v) async {
    final id = openId_;
    if (id == null) return;
    revert();
    await _engine.restoreProcedure(id, v.sha);
    await reload();
    await loadHistory();
  }

  @override
  void dispose() {
    _changes?.cancel();
    super.dispose();
  }
}
