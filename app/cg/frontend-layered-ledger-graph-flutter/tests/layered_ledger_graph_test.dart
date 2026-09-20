import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:layered_ledger_graph/layered_ledger_graph.dart';

const style = LedgerGraphStyle(
  tones: [Color(0xFF000000), Color(0xFFFF0000)],
  labelStyle: TextStyle(fontSize: 12, color: Color(0xFF000000)),
  detailStyle: TextStyle(fontSize: 10, color: Color(0xFF666666)),
  headingStyle: TextStyle(fontSize: 10, color: Color(0xFF666666)),
);

void main() {
  test('layout orders a column by its left neighbours and sizes to content', () {
    final nodes = [
      const LedgerNode(id: 'a', layer: 0, label: 'a'),
      const LedgerNode(id: 'b', layer: 0, label: 'b'),
      const LedgerNode(id: 'x', layer: 1, label: 'x'),
      const LedgerNode(id: 'y', layer: 1, label: 'y', detail: 'v'),
    ];
    final edges = [const LedgerEdge(from: 'b', to: 'x'), const LedgerEdge(from: 'a', to: 'y')];
    final layout = LedgerLayout.compute(nodes, edges, 2, style);
    expect(layout.rects['y']!.top, lessThan(layout.rects['x']!.top));
    expect(layout.rects['y']!.height, greaterThan(layout.rects['x']!.height));
    expect(layout.size.width, style.padding * 2 + 2 * style.nodeWidth + style.layerGap);
    expect(layout.hit(layout.rects['a']!.center), 'a');
    expect(layout.hit(const Offset(-1, -1)), isNull);
  });

  testWidgets('tapping a node selects it and tapping again clears', (tester) async {
    String? picked = 'unset';
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: LayeredLedgerGraph(
        nodes: const [LedgerNode(id: 'a', layer: 0, label: 'a'), LedgerNode(id: 'x', layer: 1, label: 'x', tone: 1)],
        edges: const [LedgerEdge(from: 'a', to: 'x', tone: 1, dashed: true)],
        headings: const ['L', 'R'],
        style: style,
        onSelect: (id) => picked = id,
      ),
    ));
    final layout = LedgerLayout.compute(
        const [LedgerNode(id: 'a', layer: 0, label: 'a'), LedgerNode(id: 'x', layer: 1, label: 'x', tone: 1)],
        const [LedgerEdge(from: 'a', to: 'x')], 2, style);
    await tester.tapAt(tester.getTopLeft(find.byType(LayeredLedgerGraph)) + layout.rects['a']!.center);
    expect(picked, 'a');
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: LayeredLedgerGraph(
        nodes: const [LedgerNode(id: 'a', layer: 0, label: 'a'), LedgerNode(id: 'x', layer: 1, label: 'x', tone: 1)],
        edges: const [LedgerEdge(from: 'a', to: 'x', tone: 1, dashed: true)],
        headings: const ['L', 'R'],
        style: style,
        selected: 'a',
        onSelect: (id) => picked = id,
      ),
    ));
    await tester.tapAt(tester.getTopLeft(find.byType(LayeredLedgerGraph)) + layout.rects['a']!.center);
    expect(picked, isNull);
  });
}
