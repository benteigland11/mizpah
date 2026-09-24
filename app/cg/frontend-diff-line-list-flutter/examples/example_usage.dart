import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:diff_line_list/diff_line_list.dart';

/// A change request rendered as a redline: an item appended to a list,
/// and a sentence replaced.
void main() {
  testWidgets('redline of a proposed change', (t) async {
    const style = DiffStyle(
      textStyle: TextStyle(fontSize: 16, color: Color(0xFFE6E6E6)),
    );
    await t.pumpWidget(const Directionality(
      textDirection: TextDirection.ltr,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AppendDiff(
            style: style,
            existing: ['sidecar  —  Engine sidecar protocol'],
            added: 'sandbox  —  Sandboxed shell for workers',
            trailing: 'NEEDED',
          ),
          ReplaceDiff(
            style: style,
            before: 'A loop over three tools.',
            after: 'A self-improving loop over three tools.',
          ),
        ],
      ),
    ));
    expect(find.text('+'), findsNWidgets(2));
    expect(find.text('−'), findsOneWidget);
    expect(find.text('NEEDED'), findsOneWidget);
  });
}
