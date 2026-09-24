import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ops_typography_kit/ops_typography_kit.dart';

/// A document header in the console vocabulary: a stat strip with a
/// status badge, then a ruled section with indexed rows.
void main() {
  testWidgets('console document header', (t) async {
    // Build the style once from your theme; here, hardcoded.
    const style = OpsStyle(
      labelStyle: TextStyle(fontSize: 10, letterSpacing: 1.6, color: Color(0xFF8A8A8A)),
      valueStyle: TextStyle(fontSize: 16, color: Color(0xFFE6E6E6)),
      accentColor: Color(0xFFFF6B5B),
    );
    final items = ['First item', 'Second item'];

    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          StatStrip(
            style: style,
            trailing: const Text('TRAILING'),
            children: const [
              KeyValueStat('Status', StatusBadge('active', style: style, hot: true),
                  style: style),
              KeyValueStat('Version', Text('v2'), style: style),
              KeyValueStat('Open', Text('2 OPEN'), style: style, accent: true),
            ],
          ),
          const SectionLabel('Items', style: style, centered: true,
              trailing: Text('2 TOTAL')),
          for (final (i, item) in items.indexed)
            Row(children: [RowIndex(i, style: style), Text(item)]),
        ],
      ),
    ));

    expect(find.text('STATUS'), findsOneWidget);
    expect(find.text('ACTIVE'), findsOneWidget);
    expect(find.text('ITEMS'), findsOneWidget);
    expect(find.text('01'), findsOneWidget);
    expect(find.text('02'), findsOneWidget);
  });
}
