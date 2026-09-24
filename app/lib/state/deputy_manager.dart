import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import 'brief_manager.dart';

/// The Deputy's office: the conversation on record, the turn in flight,
/// and the paper the Deputy has pulled up on the desk. The desk is its
/// own [BriefManager] — a Deputy draft, editable in place — so
/// reading it never opens a project the way the sidebar does.
class DeputyManager extends ChangeNotifier {
  DeputyManager(this._engine, this.desk) {
    _changes = _engine.changes.listen((_) => reload());
    reload();
  }

  final Engine _engine;
  final BriefManager desk;
  StreamSubscription<void>? _changes;

  List<DeputyTurn> turns = const [];

  /// The office shows the recent record, not the whole of it: the last
  /// [shown] lines, and more from the top on request (the seat's record
  /// is one conversation for good, so it only grows).
  static const page = 40;
  int shown = page;
  List<DeputyTurn> get visible => turns.length <= shown ? turns : turns.sublist(turns.length - shown);
  bool get hasEarlier => turns.length > shown;
  void loadEarlier() {
    if (!hasEarlier) return;
    shown += page;
    notifyListeners();
  }
  bool loading = true;

  /// A turn is being answered: the Deputy is thinking or running tools.
  bool busy = false;
  String? error;

  /// `{'draft': slug}` — what the desk shows; null for a clear desk.
  Map<String, dynamic>? showing;

  /// The draft's id in the project list, for the desk to select: a
  /// Deputy draft is a gym named by its slug.
  String? get showingId {
    final slug = showing?['draft'] as String?;
    return slug == null ? null : 'gyms/$slug';
  }

  Future<void> reload() async {
    turns = await _engine.readDeputyTurns();
    showing = await _engine.readDeputyShowing();
    steps = await _engine.readDeputySteps();
    loading = false;
    await _followDesk();
    notifyListeners();
  }

  /// The desk follows `showing`: select the draft when it appears in the
  /// list, clear when it is gone. An unsaved edit on the desk is kept.
  Future<void> _followDesk() async {
    final id = showingId;
    await desk.refreshList();
    if (id == null || !desk.briefs.any((b) => b.id == id)) {
      if (desk.selectedId != null && !desk.dirty) desk.deselect();
      return;
    }
    if (desk.selectedId != id) await desk.select(id);
  }

  /// Say something. The person's line shows at once; the reply lands when
  /// the engine has it. A failed turn is a system line, not a dialog.
  /// A line that could not be delivered, for the say line to restore.
  String? unsent;

  Future<void> send(String text) async {
    final said = text.trim();
    if (said.isEmpty || busy) return;
    busy = true;
    stopping = false;
    error = null;
    // This turn's steps start after now: until the journal shows a step
    // later than this, what it holds is the last turn's, not to be shown
    // under the message just sent.
    _sentAt = DateTime.now().subtract(const Duration(seconds: 2));
    steps = const [];
    turns = [...turns, DeputyTurn(at: DateTime.now().toUtc(), role: 'user', text: said)];
    notifyListeners();
    _watch = Timer.periodic(const Duration(milliseconds: 600), (_) => _readActivity());
    try {
      await _engine.deputySay(said);
      unsent = null;
    } catch (e) {
      error = '$e';
      // The seat never heard it (the engine records only what it heard),
      // so the words go back in the box rather than into the record.
      unsent = said;
    }
    _watch?.cancel();
    _watch = null;
    busy = false;
    stopping = false;
    activity = null;
    await reload();
  }

  /// What the Deputy is doing right now, while [busy]: the command it is
  /// running, or thinking. Read off the engine's activity file as it moves.
  Map<String, dynamic>? activity;
  Timer? _watch;

  /// The turn's steps so far — every model call, tool call and compaction
  /// with its time — live while [busy]; afterwards the finished turn's.
  List<DeputyStep> steps = const [];

  /// A stop was asked for; the turn ends at its next step.
  bool stopping = false;

  /// When the last message was sent, less a little slack for clocks.
  DateTime? _sentAt;

  Future<void> _readActivity() async {
    if (!busy) return;
    final a = await _engine.readDeputyActivity();
    final fresh = await _engine.readDeputySteps();
    if (!busy) return;
    // Steps from before the send are the previous turn's: the engine has
    // not opened this one yet.
    final sent = _sentAt;
    steps = fresh.isNotEmpty && sent != null && fresh.first.at.isBefore(sent) ? const [] : fresh;
    notifyListeners(); // the elapsed counter on the open step ticks
    final was = activity == null ? '' : '${activity!['kind']}/${activity!['command'] ?? ''}';
    final now = a == null ? '' : '${a['kind']}/${a['command'] ?? ''}';
    if (was == now) return;
    activity = a;
    notifyListeners();
  }

  /// End the turn under way at its next step. The reply so far, and the
  /// tool calls made, stay on record; the conversation continues after.
  Future<void> stop() async {
    if (!busy || stopping) return;
    stopping = true;
    notifyListeners();
    try {
      await _engine.stopDeputy();
    } catch (e) {
      error = '$e';
      stopping = false;
      notifyListeners();
    }
  }

  /// The desk cleared by hand: the Deputy's memory of it stands.
  void clearDesk() {
    showing = null;
    if (!desk.dirty) desk.deselect();
    notifyListeners();
  }

  /// Clear everything: the Deputy forgets, and the conversation goes off
  /// the desk (kept aside on disk by the engine). The paper shown clears too.
  Future<void> reset() async {
    await _engine.resetDeputy();
    showing = null;
    if (!desk.dirty) desk.deselect();
    await reload();
  }

  @override
  void dispose() {
    _changes?.cancel();
    _watch?.cancel();
    super.dispose();
  }
}
