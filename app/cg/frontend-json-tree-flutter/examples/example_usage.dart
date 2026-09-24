import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:json_tree/json_tree.dart';

/// Show a task record two levels deep and report which node was tapped.
void main() {
  testWidgets('browse a task record', (tester) async {
    const text = '{"task": {"id": "item-1", "bucket": "medium", "points": 8,'
        ' "acceptance": ["one", "two"], "evidence": []}}';
    String? tapped;
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: JsonTree(
        value: parseJsonDocument(text),
        expandDepth: 2,
        onSelect: (path) => tapped = path,
      ),
    ));
    await tester.tap(find.textContaining('"acceptance"', findRichText: true));
    await tester.pump();
    expect(tapped, r'$.task.acceptance');
    expect(find.textContaining('0: "one"', findRichText: true), findsOneWidget);
  });
}
