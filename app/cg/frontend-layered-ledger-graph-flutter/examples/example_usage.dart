import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:layered_ledger_graph/layered_ledger_graph.dart';

void main() {
  testWidgets('three columns of items with threads between them', (tester) async {
    const style = LedgerGraphStyle(
      tones: [Color(0xFF222222), Color(0xFFB00020), Color(0xFF1E5AA8), Color(0xFF9E9E9E)],
      labelStyle: TextStyle(fontSize: 13, color: Color(0xFF222222)),
      detailStyle: TextStyle(fontSize: 11, color: Color(0xFF666666)),
      headingStyle: TextStyle(fontSize: 11, color: Color(0xFF666666)),
    );
    await tester.pumpWidget(const Directionality(
      textDirection: TextDirection.ltr,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: LayeredLedgerGraph(
          nodes: [
            LedgerNode(id: 'q1', layer: 0, label: 'Question one', tone: 0),
            LedgerNode(id: 'q2', layer: 0, label: 'Question two', tone: 1),
            LedgerNode(id: 'r1', layer: 1, label: 'reading_one', detail: '42 items · n 3 · high'),
            LedgerNode(id: 'r2', layer: 1, label: 'reading_two', detail: 'false · n 3 · med', tone: 1),
            LedgerNode(id: 'w1', layer: 2, label: 'order_one', tone: 3),
          ],
          edges: [
            LedgerEdge(from: 'q1', to: 'r1'),
            LedgerEdge(from: 'q2', to: 'r2', tone: 1),
            LedgerEdge(from: 'r1', to: 'w1'),
            LedgerEdge(from: 'r2', to: 'w1', tone: 1, dashed: true),
          ],
          headings: ['QUESTIONS', 'READINGS', 'ORDERS'],
          style: style,
        ),
      ),
    ));
    expect(find.byType(LayeredLedgerGraph), findsOneWidget);
  });
}
