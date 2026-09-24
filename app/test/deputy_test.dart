import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/engine.dart';

void main() {
  test('a deputy turn reads back from the engine log line', () {
    final t = DeputyTurn.fromJson({
      'at': 1789883199.29,
      'role': 'deputy',
      'text': 'Drafted and shown.',
      'showing': {'draft': 'ornith-landing'},
      'seconds': 39.0,
      'tool_calls': 8,
    });
    expect(t.role, 'deputy');
    expect(t.showing, {'draft': 'ornith-landing'});
    expect(t.at.toUtc().year, 2026);
    expect(t.toolCalls, 8);
    expect(t.error, isFalse);
    final s = DeputyTurn.fromJson({'role': 'system', 'text': 'no answer', 'error': true});
    expect(s.error, isTrue);
    expect(s.showing, isNull);
  });
}
