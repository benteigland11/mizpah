import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/engine.dart';

/// What wants a person, across every project. Re-reads on the engine's
/// tick; the per-project inbox stores make that cheap.
class InboxManager extends ChangeNotifier {
  InboxManager(this._engine) {
    _changes = _engine.changes.listen((_) => reload());
    // Marks first, then the tray, so nothing already read flashes unread —
    // and nothing is written back before they are in. A tick from the
    // engine before the marks loaded used to reload, mark the opened sheet
    // read and save: an empty set of dismissals over the file, so every
    // sheet set aside came back at the next launch.
    _loadAcks().whenComplete(() {
      _marksLoaded = true;
      reload();
    });
  }

  bool _marksLoaded = false;

  final Engine _engine;
  StreamSubscription<void>? _changes;

  /// Two marks on paper, both kept by the engine so they survive launches
  /// and follow the desk to a browser, keyed by task, kind and number:
  /// **read** (it was opened, or marked so) and **dismissed** (× — off the
  /// tray). A signature owed can be read but never dismissed — only signed.
  final Set<String> _acked = {};
  final Set<String> _read = {};

  /// One key per sheet. Anomalies and notices carry no number, and a
  /// project has many of them (every outage is one): the time and stamp
  /// tell them apart, else dismissing one dismissed them all.
  static String keyOf(AttentionItem i) {
    final d = i.document;
    final id = d.number.isNotEmpty
        ? d.number
        : '${d.at?.toUtc().millisecondsSinceEpoch ?? 0}|${d.status}|${d.header.map((h) => h.$2).join(',')}';
    return '${i.project.id}|${d.kind.name}|$id';
  }

  bool isAcknowledged(AttentionItem i) => _acked.contains(keyOf(i));

  bool isRead(AttentionItem i) => _read.contains(keyOf(i));

  Future<void> _loadAcks() async {
    try {
      _take(await _engine.readAcknowledged());
    } catch (_) {}
  }

  /// A mark goes to the engine as a delta and the record comes back
  /// whole, so this desk and any other (a browser) converge on one file.
  void _mark({List<String> read = const [], List<String> unread = const [], List<String> dismissed = const []}) {
    if (!_marksLoaded) return;
    _engine.markAcknowledged(read: read, unread: unread, dismissed: dismissed).then(_take).catchError((_) {});
  }

  void _take(Map<String, dynamic> j) {
    _read
      ..clear()
      ..addAll((j['read'] as List? ?? const []).cast<String>());
    // Setting aside only ever adds, so the set only grows here: a record
    // read before a dismissal landed must not bring the sheet back.
    _acked.addAll((j['dismissed'] as List? ?? const []).cast<String>());
  }

  /// Opening a document reads it.
  void markRead(AttentionItem i) {
    if (_read.add(keyOf(i))) {
      _mark(read: [keyOf(i)]);
      notifyListeners();
    }
  }

  void markUnread(AttentionItem i) {
    if (_read.remove(keyOf(i))) {
      _mark(unread: [keyOf(i)]);
      notifyListeners();
    }
  }

  /// What the badge counts: signatures owed, and anything else unread.
  int get unread => items.where((i) => i.document.awaitingSignature || !isRead(i)).length;

  /// Set a document aside. Returns false for paper that needs a signature.
  bool dismiss(AttentionItem i) {
    if (i.document.awaitingSignature) return false;
    final k = keyOf(i);
    // Setting aside the open sheet opens the one that takes its place in
    // the tray (the one above, at the foot) — never the top of the list.
    final at = items.indexWhere((x) => keyOf(x) == k);
    if (k == _openKey && at >= 0) {
      final rest = [for (final x in items) if (keyOf(x) != k) x];
      _openKey = rest.isEmpty ? null : keyOf(rest[at.clamp(0, rest.length - 1)]);
    }
    _acked.add(k);
    _mark(dismissed: [k]);
    _apply();
    return true;
  }

  /// Everything the engine reported, before dismissals.
  List<AttentionItem> _all = const [];

  void _apply() {
    items = _all.where((i) => !isAcknowledged(i)).toList();
    final ix = _openKey == null ? -1 : items.indexWhere((i) => keyOf(i) == _openKey);
    // The sheet you had open stays open. With the tray in front of you
    // and nothing open (you just walked in, or set the open sheet aside),
    // the first sheet opens — and reads, since you are looking at it. Off
    // screen nothing opens: a sheet opened unseen counted as read, so
    // three arrivals showed as two.
    _openKey = ix >= 0 ? _openKey : (visible && items.isNotEmpty ? keyOf(items.first) : null);
    final showing = opened;
    if (visible && showing != null && _read.add(keyOf(showing))) _mark(read: [keyOf(showing)]);
    notifyListeners();
  }

  /// Whether the tray is the page in front of the person. Set by the shell
  /// from the tab; walking in opens the first sheet.
  bool get visible => _visible;
  bool _visible = false;
  set visible(bool v) {
    if (v == _visible) return;
    _visible = v;
    if (v) _apply();
  }

  List<AttentionItem> items = const [];
  bool loading = true;

  /// The open sheet, by key: a re-read builds new items, and an index
  /// or an identity would point at whatever moved into its place.
  String? _openKey;
  String? get openKey => _openKey;

  AttentionItem? get opened =>
      _openKey == null ? null : items.where((i) => keyOf(i) == _openKey).firstOrNull;

  int get signatures => items.where((i) => i.document.awaitingSignature).length;

  void select(AttentionItem i) {
    _openKey = keyOf(i);
    markRead(i);
    notifyListeners();
  }

  /// The next or previous sheet in the tray, for the keyboard.
  void step(int by) {
    if (items.isEmpty) return;
    final at = _openKey == null ? -1 : items.indexWhere((i) => keyOf(i) == _openKey);
    select(items[(at < 0 ? (by > 0 ? 0 : items.length - 1) : at + by).clamp(0, items.length - 1)]);
  }

  /// Set the open sheet aside, for the keyboard.
  void dismissOpen() {
    final i = opened;
    if (i != null) dismiss(i);
  }

  /// Sign a change request from the in-tray: straight to the engine for
  /// that project, then re-read. The caller refreshes the brief manager if
  /// this project happens to be the open one.
  Future<void> decide(AttentionItem item, {required bool accept, String reason = ''}) async {
    final pid = item.document.proposalId;
    if (pid == null) return;
    if (accept) {
      await _engine.acceptProposal(item.project.id, pid, reason: reason);
    } else {
      await _engine.rejectProposal(item.project.id, pid, reason: reason);
    }
    await reload();
  }

  Future<void> reload() async {
    if (!_marksLoaded) return; // the constructor reloads once they are
    // Marks first: another desk may have read or set aside since.
    final gen = ++_reloads;
    await _loadAcks();
    final all = await _engine.readAttention();
    // Ticks overlap; a slower, older read must not land over a newer one.
    if (gen != _reloads) return;
    _all = all;
    loading = false;
    _apply();
  }

  int _reloads = 0;

  @override
  void dispose() {
    _changes?.cancel();
    super.dispose();
  }
}
