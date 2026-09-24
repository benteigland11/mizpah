import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:segmented_capacity_bar/segmented_capacity_bar.dart';

Widget host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 202, child: child)),
    );

const green = Color(0xFF00FF00);
const grey = Color(0xFF888888);

void main() {
  testWidgets('segments are proportional to capacity', (t) async {
    // 202 wide with a 1px border each side leaves 200 for the row.
    await t.pumpWidget(host(const SegmentedCapacityBar(
      capacity: 100,
      segments: [CapacitySegment(25, green), CapacitySegment(50, grey)],
    )));
    final boxes = find.byType(ColoredBox);
    expect(boxes, findsNWidgets(2));
    expect(t.getSize(boxes.at(0)).width, closeTo(50, 0.5));
    expect(t.getSize(boxes.at(1)).width, closeTo(100, 0.5));
  });

  testWidgets('zero segments are skipped, empty capacity is fine',
      (t) async {
    await t.pumpWidget(host(const SegmentedCapacityBar(
      capacity: 0,
      segments: [CapacitySegment(0, green)],
    )));
    expect(find.byType(ColoredBox), findsNothing);
  });

  testWidgets('overrun fills the bar and switches the outline', (t) async {
    await t.pumpWidget(host(const SegmentedCapacityBar(
      capacity: 10,
      overrunColor: Color(0xFFFF0000),
      segments: [CapacitySegment(8, green), CapacitySegment(6, grey)],
    )));
    final boxes = find.byType(ColoredBox);
    final total = t.getSize(boxes.at(0)).width + t.getSize(boxes.at(1)).width;
    expect(total, closeTo(200, 0.5));
    final deco = t.widget<Container>(find.byType(Container)).decoration
        as BoxDecoration;
    expect(deco.border!.top.color, const Color(0xFFFF0000));
  });

  testWidgets('no capacity: bar scales to the segments', (t) async {
    await t.pumpWidget(host(const SegmentedCapacityBar(
      segments: [CapacitySegment(1, green), CapacitySegment(3, grey)],
    )));
    final boxes = find.byType(ColoredBox);
    expect(t.getSize(boxes.at(0)).width, closeTo(50, 0.5));
    expect(t.getSize(boxes.at(1)).width, closeTo(150, 0.5));
  });

  test('readout arithmetic', () {
    final r = CapacityReadout.of(
        const [CapacitySegment(11, green), CapacitySegment(32, grey)], 89);
    expect(r.used, 43);
    expect(r.remaining, 46);
    expect(r.over, isFalse);
    expect(r.overBy, 0);
    final o = CapacityReadout.of(const [CapacitySegment(50, green)], 40);
    expect(o.over, isTrue);
    expect(o.overBy, 10);
    expect(CapacityReadout.of(const [], null).remaining, isNull);
  });
}
