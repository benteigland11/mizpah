import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/brief.dart';
import '../models/route.dart';

/// Which brief is open and the draft being edited.
class BriefManager extends ChangeNotifier {
  BriefManager(this._engine) {
    _changes = _engine.changes.listen((_) => refreshList());
  }

  final Engine _engine;
  StreamSubscription<void>? _changes;

  /// Re-list projects without touching the open draft: a run appeared,
  /// stopped, or now wants attention.
  Future<void> refreshList() async {
    briefs = await _engine.listBriefs();
    if (selectedId != null && !briefs.any((b) => b.id == selectedId)) {
      deselect();
      return;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _changes?.cancel();
    super.dispose();
  }

  List<BriefSummary> briefs = const [];
  String? selectedId;
  Brief? draft;
  RouteStatus route = RouteStatus.empty;

  /// Id of the proposal open in the main area, or null for the brief.
  String? viewingProposal;
  String? error;
  bool saving = false;

  /// Bumped whenever [draft] is replaced wholesale (select / revert) so the
  /// form knows to rebuild its text controllers rather than just repaint.
  int generation = 0;

  /// Identity of a row added this session that should open in edit mode.
  Object? focusKey;

  String _loaded = '';

  bool get dirty => draft != null && jsonEncode(draft!.toJson()) != _loaded;

  /// The selected brief's project root, from the listing.
  String get selectedPath =>
      briefs.where((b) => b.id == selectedId).firstOrNull?.path ?? '';

  /// Lists projects. Nothing is selected until a person walks into one:
  /// the app opens on Home, not in an office.
  Future<void> load() async {
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  /// Back to Home: no project open.
  void deselect() {
    selectedId = null;
    draft = null;
    viewingProposal = null;
    route = RouteStatus.empty;
    generation++;
    notifyListeners();
  }

  Future<void> select(String id) async {
    selectedId = id;
    error = null;
    // Normalise through the model so key order can't fake a dirty state.
    draft = Brief.fromJson(await _engine.readBrief(id));
    _loaded = jsonEncode(draft!.toJson());
    route = await _engine.readRoute(id);
    generation++;
    notifyListeners();
  }

  /// Apply an edit to the draft and repaint.
  void edit(void Function(Brief b) change) {
    final b = draft;
    if (b == null) return;
    focusKey = null;
    change(b);
    error = null;
    notifyListeners();
  }

  /// Like [edit] but leaves [key] flagged so its row opens for typing.
  void add(Object key, void Function(Brief b) change) {
    edit(change);
    focusKey = key;
  }

  Future<void> save() async {
    final id = selectedId;
    final b = draft;
    if (id == null || b == null || saving || !dirty) return;
    saving = true;
    notifyListeners();
    try {
      await _engine.writeBrief(id, b.toJson());
      // The engine owns the record after a write (it bumps version, may
      // normalise); re-read so the draft matches what's on disk.
      draft = Brief.fromJson(await _engine.readBrief(id));
      _loaded = jsonEncode(draft!.toJson());
      generation++;
      error = null;
      briefs = await _engine.listBriefs();
    } catch (e) {
      error = '$e';
    } finally {
      saving = false;
      notifyListeners();
    }
  }

  List<Proposal> get openProposals =>
      draft?.proposals.where((p) => p.status == 'open').toList() ?? const [];

  void openProposal(String? id) {
    viewingProposal = id;
    notifyListeners();
  }

  /// Open the first proposal waiting, or nothing if the queue is empty.
  void openQueue() => openProposal(openProposals.firstOrNull?.id);

  /// Accept or reject, then reload: the engine owns the brief after this.
  /// Refused while there are unsaved edits, so they can't be silently lost.
  /// Afterwards, move to the proposal that followed this one in the queue
  /// (or the last one left), or back to the brief when the queue is empty.
  Future<void> decide(
    String proposalId, {
    required bool accept,
    String reason = '',
  }) async {
    final id = selectedId;
    if (id == null || dirty) return;
    final before = openProposals.map((p) => p.id).toList();
    try {
      if (accept) {
        await _engine.acceptProposal(id, proposalId, reason: reason);
      } else {
        await _engine.rejectProposal(id, proposalId, reason: reason);
      }
      await select(id);
      final after = openProposals.map((p) => p.id).toSet();
      final following = before.skip(before.indexOf(proposalId) + 1);
      viewingProposal = after.isEmpty
          ? null
          : following.firstWhere(after.contains, orElse: () => after.last);
      briefs = await _engine.listBriefs();
      notifyListeners();
    } catch (e) {
      error = '$e';
      notifyListeners();
    }
  }

  void revert() {
    draft = Brief.fromJson(jsonDecode(_loaded) as Map<String, dynamic>);
    error = null;
    generation++;
    notifyListeners();
  }
}
