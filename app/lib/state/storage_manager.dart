import 'package:flutter/foundation.dart';

import '../engine/engine.dart';
import '../models/storage.dart';

export '../models/storage.dart';

/// What the tasks folder weighs and the one thing that can be cleared
/// from it: the workers' raw transcripts. The engine does the walking.
class StorageManager extends ChangeNotifier {
  StorageManager(this._engine);
  final Engine _engine;

  StorageReport? report;
  bool measuring = false;
  bool clearing = false;
  String? lastAction;

  Future<void> measure() async {
    if (measuring) return;
    measuring = true;
    notifyListeners();
    report = await _engine.measureStorage();
    measuring = false;
    notifyListeners();
  }

  /// Delete every worker transcript under a loop that is not running.
  /// Paperwork, maps, runs and results stay; only `events/session.jsonl`
  /// goes. A live loop's logs are left alone.
  Future<void> clearTranscripts() async {
    if (clearing) return;
    clearing = true;
    notifyListeners();
    final freed = await _engine.clearTranscripts();
    clearing = false;
    lastAction = 'Cleared ${human(freed)} of worker transcripts.';
    notifyListeners();
    await measure();
  }

  static String human(int bytes) => humanBytes(bytes);
}
