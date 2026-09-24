import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:segmented_capacity_bar/segmented_capacity_bar.dart';

/// A budget bar: points done and reserved against a total, with a readout.
void main() {
  testWidgets('budget bar with readout', (t) async {
    const segments = [
      CapacitySegment(11, Color(0xFFFF6B5B)), // done
      CapacitySegment(32, Color(0xFF8A8A8A)), // reserved
    ];
    final readout = CapacityReadout.of(segments, 89);

    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Column(
        children: [
          const SizedBox(
            width: 260,
            child: SegmentedCapacityBar(segments: segments, capacity: 89),
          ),
          Text('${readout.used} USED · ${readout.remaining} AVAILABLE'),
        ],
      ),
    ));
    expect(find.text('43 USED · 46 AVAILABLE'), findsOneWidget);
  });
}
