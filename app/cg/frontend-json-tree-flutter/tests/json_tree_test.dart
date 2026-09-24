import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:json_tree/json_tree.dart';

final doc = {
  'task': {'id': 'item-1', 'points': 8, 'ok': true, 'none': null},
  'list': [for (var i = 0; i < 5; i++) i],
  'odd key': 'x' * 400,
};

Future<void> host(WidgetTester t, Widget w) => t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Align(alignment: Alignment.topLeft, child: SizedBox(width: 400, height: 600, child: w)),
    ));

bool shows(String s) => find.textContaining(s, findRichText: true).evaluate().isNotEmpty;

void main() {
  test('parseJsonDocument: JSON, JSON Lines, errors', () {
    expect(parseJsonDocument('{"a": 1}'), {'a': 1});
    expect(parseJsonDocument('{"a": 1}\n\n{"a": 2}\n'), [
      {'a': 1},
      {'a': 2}
    ]);
    expect(() => parseJsonDocument('{oops'), throwsFormatException);
    expect(() => parseJsonDocument('{"a": 1}\nnot json'), throwsFormatException);
  });

  test('jsonPath', () {
    expect(jsonPath(const []), r'$');
    expect(jsonPath(const ['items', 2, 'name']), r'$.items[2].name');
    expect(jsonPath(const ['odd key', '1']), r'$["odd key"]["1"]');
  });

  testWidgets('root open, children folded with counts; tap folds and unfolds', (t) async {
    final picked = <String>[];
    await host(t, JsonTree(value: doc, onSelect: picked.add));
    expect(shows('3 keys'), isTrue);
    expect(shows('"task": {4 keys}'), isTrue);
    expect(shows('"list": [5 items]'), isTrue);
    expect(shows('"id"'), isFalse);

    await t.tap(find.textContaining('"task"', findRichText: true));
    await t.pump();
    expect(shows('"id": "item-1"'), isTrue);
    expect(shows('"points": 8'), isTrue);
    expect(shows('"ok": true'), isTrue);
    expect(shows('"none": null'), isTrue);
    expect(picked.last, r'$.task');

    await t.tap(find.textContaining('"task"', findRichText: true));
    await t.pump();
    expect(shows('"id"'), isFalse);

    // A long string unwraps on tap.
    await t.tap(find.textContaining('"odd key"', findRichText: true));
    await t.pump();
    expect(picked.last, r'$["odd key"]');
    await t.tap(find.textContaining('"odd key"', findRichText: true));
    await t.pump();
  });

  testWidgets('paging big arrays and objects', (t) async {
    await host(t, JsonTree(value: {
      'big': [for (var i = 0; i < 7; i++) i],
      'map': {for (var i = 0; i < 4; i++) 'k$i': i},
    }, expandDepth: 2, pageSize: 3));
    expect(shows('… 4 more — show 3'), isTrue);
    expect(shows('… 1 more — show 1'), isTrue);
    await t.tap(find.textContaining('… 4 more'));
    await t.pump();
    expect(shows('… 1 more — show 1'), isTrue);
    expect(shows('5: 5'), isTrue);
  });

  testWidgets('controller expands and collapses all; new value resets', (t) async {
    final c = JsonTreeController();
    await host(t, JsonTree(value: doc, controller: c, expandDepth: 0));
    expect(shows('"task"'), isFalse);
    c.expandAll();
    await t.pump();
    expect(shows('"id": "item-1"'), isTrue);
    expect(shows('4: 4'), isTrue);
    c.collapseAll();
    await t.pump();
    expect(shows('"task": {4 keys}'), isTrue);
    expect(shows('"id"'), isFalse);

    final c2 = JsonTreeController();
    await host(t, JsonTree(value: const [1, 'a', false], controller: c2, shrinkWrap: true));
    expect(shows('0: 1'), isTrue);
    expect(shows('1: "a"'), isTrue);
    c.expandAll(); // the old controller no longer drives it
    await t.pump();
    c2.collapseAll();
    await t.pump();
    expect(shows('2: false'), isTrue);
    await host(t, const JsonTree(value: 42, style: JsonTreeStyle(indent: 10)));
    expect(shows('42'), isTrue);
    expect(t.takeException(), isNull);
  });
}
