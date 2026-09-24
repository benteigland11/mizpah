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

  /// The saved gym environments a brief may name.
  Future<List<Map<String, String>>> environments() => _engine.environments();
  Future<void> setDefaultEnvironment(String name) => _engine.setDefaultEnvironment(name);
  StreamSubscription<void>? _changes;

  /// Re-list projects without touching the open draft: a run appeared,
  /// stopped, or now wants attention.
  Future<void> refreshList() async {
    briefs = await _engine.listBriefs();
    if (selectedId != null && !briefs.any((b) => b.id == selectedId)) {
      deselect();
      return;
    }
    await _syncOpen();
    notifyListeners();
  }

  /// The open brief follows the disk while no edit is pending: the loop
  /// accepted a proposal, a change request arrived, a version bumped. An
  /// unsaved draft is the person's and is left alone.
  Future<void> _syncOpen() async {
    final id = selectedId;
    if (id == null || draft == null || dirty) return;
    final fresh = Brief.fromJson(await _engine.readBrief(id));
    if (selectedId != id) return;
    final encoded = jsonEncode(fresh.toJson());
    if (encoded == _loaded) return;
    draft = fresh;
    _loaded = encoded;
    route = await _engine.readRoute(id);
    generation++;
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

  /// A task's brief as it sits on disk, without selecting it — for a
  /// sheet that shows another task's paper (Home).
  Future<Brief> peek(String id) async => Brief.fromJson(await _engine.readBrief(id));

  /// Open a new task: the engine makes the folder and a blank brief, the
  /// list refreshes, and the new task is selected so the person can write.
  Future<String> create(String title, {required String mission, String? repo}) async {
    final id = await _engine.createTask(title, mission: mission, repo: repo);
    briefs = await _engine.listBriefs();
    if (id.isNotEmpty) await select(id);
    notifyListeners();
    return id;
  }

  /// Sign and issue the open draft: the loop starts; the brief re-reads.
  /// A draft is first made a project (furnished where it is, or moved
  /// into [repo], where the id changes and the desk follows it), then
  /// issued and started.
  Future<void> issue({String? repo}) async {
    var id = selectedId;
    if (id == null || dirty) return;
    id = await _engine.authorizeDraft(id, repo: repo);
    selectedId = id;
    await _engine.startTask(id);
    await select(id);
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  /// Start a brief that was signed and never ran (a signature with no
  /// session: the loop was never launched, or its sessions were removed).
  Future<void> start() async {
    final id = selectedId;
    if (id == null) return;
    await _engine.startTask(id);
    await select(id);
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  /// The project as a whole, from its row. Archive and unarchive keep the
  /// desk where it is; delete leaves it (the project is gone) — unless it
  /// was a draft, which discards.
  Future<void> archiveProject(String id) async {
    await _engine.archiveProject(id);
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  Future<void> unarchiveProject(String id) async {
    await _engine.unarchiveProject(id);
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  Future<void> deleteProject(String id) async {
    final summary = briefs.where((b) => b.id == id).firstOrNull;
    if (summary?.state == 'idle') {
      await _engine.discardDraft(id);
    } else {
      await _engine.deleteProject(id);
    }
    if (selectedId == id) deselect();
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  /// Throw the open draft away: the desk clears and the list re-reads.
  Future<void> discard() async {
    final id = selectedId;
    if (id == null) return;
    await _engine.discardDraft(id);
    deselect();
    briefs = await _engine.listBriefs();
    notifyListeners();
  }

  /// Whether a path on the desk is a folder and a git repository.
  Future<Map<String, bool>> inspectPath(String path) => _engine.inspectPath(path);

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
