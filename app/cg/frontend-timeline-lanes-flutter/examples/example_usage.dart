import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:timeline_lanes/timeline_lanes.dart';

/// A tempo lane, a pedal lane and a velocity lane stacked under one
/// shared window; dragging any of them pans all three.
void main() {
  testWidgets('three lanes on one window', (tester) async {
    var start = 0.0;
    const span = 8.0;
    late StateSetter rebuild;

    Widget lanes() => Column(children: [
          TimelineLane.step(
            label: 'Tempo',
            points: const [
              LanePoint(0, 120),
              LanePoint(4, 100),
              LanePoint(12, 80)
            ],
            start: start,
            span: span,
            format: (v) => '${v.round()} bpm',
            onPanTime: (dt) => rebuild(() => start = (start + dt).clamp(0, 8)),
          ),
          TimelineLane.onOff(
            label: 'Pedal',
            spans: const [LaneSpan(0, 3.5), LaneSpan(4, 7.5)],
            start: start,
            span: span,
            onPanTime: (dt) => rebuild(() => start = (start + dt).clamp(0, 8)),
          ),
          TimelineLane.stalks(
            label: 'Velocity',
            points: [
              for (var i = 0; i < 16; i++)
                LanePoint(i.toDouble(), 60 + (i % 4) * 15.0)
            ],
            start: start,
            span: span,
            min: 0,
            max: 127,
            onPanTime: (dt) => rebuild(() => start = (start + dt).clamp(0, 8)),
          ),
        ]);

    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Align(
        alignment: Alignment.topLeft,
        child: SizedBox(
          width: 444,
          child: StatefulBuilder(builder: (context, setState) {
            rebuild = setState;
            return lanes();
          }),
        ),
      ),
    ));
    await tester.dragFrom(const Offset(244, 50), const Offset(-200, 0));
    await tester.pump();
    expect(start, closeTo(4, 0.1));
  });
}
