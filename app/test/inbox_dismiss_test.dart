import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/engine.dart';
import 'package:mizpah_app/models/document.dart';
import 'package:mizpah_app/state/inbox_manager.dart';

/// Only what the tray calls; the marks answer late, as over the wire.
class _Tray implements Engine {
  _Tray(this.docs);
  List<AttentionItem> docs;
  Map<String, dynamic> marks = {'read': <String>[], 'dismissed': <String>[]};
  final _changes = StreamController<void>.broadcast(); // ignore: close_sinks
  Completer<void>? hold;

  @override
  Stream<void> get changes => _changes.stream;
  @override
  Future<List<AttentionItem>> readAttention() async => [...docs];
  @override
  Future<Map<String, dynamic>> readAcknowledged() async {
    final stale = {...marks};
    await hold?.future;
    return stale;
  }

  @override
  Future<Map<String, dynamic>> markAcknowledged({
    List<String> read = const [],
    List<String> unread = const [],
    List<String> dismissed = const [],
  }) async {
    marks = {
      'read': {...marks['read'] as List, ...read}.difference(unread.toSet()).toList(),
      'dismissed': {...marks['dismissed'] as List, ...dismissed}.toList(),
    };
    return marks;
  }

  @override
  dynamic noSuchMethod(Invocation i) => super.noSuchMethod(i);
}

AttentionItem _notice(int n) => AttentionItem(
      project: BriefSummary(id: 'p$n', title: 'Task $n', path: '/p$n'),
      document: InboxDocument(
        kind: DocKind.completion,
        number: '',
        title: 'Complete',
        at: DateTime.utc(2026, 9, 22, 12, n),
        from: 'loop',
        status: 'COMPLETE',
        header: const [],
        sections: const [],
      ),
    );

void main() {
  late _Tray engine;
  late InboxManager inbox;

  Future<void> settle() => Future<void>.delayed(Duration.zero);

  setUp(() async {
    engine = _Tray([for (var n = 1; n <= 4; n++) _notice(n)]);
    inbox = InboxManager(engine)..visible = true;
    await settle();
    await settle();
  });

  List<String> titles() => [for (final i in inbox.items) i.project.title];

  test('setting aside the open sheet opens its neighbour, not the top', () async {
    inbox.select(inbox.items[2]);
    inbox.dismissOpen();
    expect(titles(), ['Task 1', 'Task 2', 'Task 4']);
    expect(inbox.opened!.project.title, 'Task 4');
    inbox.dismissOpen(); // the foot: the one above opens
    expect(inbox.opened!.project.title, 'Task 2');
  });

  test('a re-read keeps the open sheet by what it is, not where it was', () async {
    inbox.select(inbox.items[1]); // Task 2
    engine.docs = [_notice(9), ...engine.docs];
    await inbox.reload();
    expect(inbox.opened!.project.title, 'Task 2');
  });

  test('a record read before the dismissal landed does not bring it back', () async {
    engine.hold = Completer();
    final reading = inbox.reload(); // reads the marks before the dismissal
    await settle();
    inbox.dismiss(inbox.items.first);
    engine.hold!.complete();
    await reading;
    expect(titles(), ['Task 2', 'Task 3', 'Task 4']);
  });
}
