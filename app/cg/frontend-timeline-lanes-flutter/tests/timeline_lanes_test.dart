import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:timeline_lanes/timeline_lanes.dart';

// 444 wide: a 44 label column leaves 400 for times 0-4, 100px per unit.
Future<void> pump(WidgetTester t, Widget lane) => t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(width: 444, child: lane)),
    ));

Future<TestGesture> hoverAt(WidgetTester t, double time,
    {double dy = 20}) async {
  final m = await t.createGesture(kind: PointerDeviceKind.mouse);
  await m.addPointer(location: const Offset(700, 500));
  await m.moveTo(Offset(44 + time * 100, dy));
  await t.pump();
  return m;
}

const grid = [
  LaneGridLine(0, bar: true),
  LaneGridLine(1),
  LaneGridLine(9, bar: true)
];

void main() {
  testWidgets('step lane shows the held value under the pointer', (t) async {
    await pump(
        t,
        const TimelineLane.step(
          label: 'Tempo',
          points: [LanePoint(2, 90), LanePoint(0, 120)], // unsorted on purpose
          start: 0,
          span: 4,
          grid: grid,
        ));
    expect(find.text('Tempo'), findsOneWidget);
    final m = await hoverAt(t, 1);
    expect(find.text('120'), findsOneWidget);
    await m.moveTo(const Offset(44 + 300, 20));
    await t.pump();
    expect(find.text('90'), findsOneWidget);
    await m.moveTo(const Offset(10, 20)); // over the label column
    await t.pump();
    expect(find.text('90'), findsNothing);
    await m.moveTo(const Offset(44 + 300, 20));
    await t.pump();
    await m.moveTo(const Offset(44 + 300, 200)); // leave
    await t.pump();
    expect(find.text('90'), findsNothing);
    await m.removePointer();
  });

  testWidgets(
      'curve lane interpolates; format applies; before the first point is empty',
      (t) async {
    await pump(
        t,
        TimelineLane.step(
          label: 'Bend',
          curve: true,
          points: const [LanePoint(1, 0), LanePoint(3, 100)],
          start: 0,
          span: 4,
          min: -8192,
          max: 8191,
          end: 3.5,
          format: (v) => '${v.round()}c',
        ));
    final m = await hoverAt(t, 2);
    expect(find.text('50c'), findsOneWidget);
    await m.moveTo(const Offset(44 + 50, 20));
    await t.pump();
    expect(find.textContaining('c', findRichText: false), findsNothing);
    await m.moveTo(const Offset(44 + 350, 20)); // after the last point
    await t.pump();
    expect(find.text('100c'), findsOneWidget);
    await m.removePointer();
  });

  testWidgets('on/off lane', (t) async {
    await pump(
        t,
        const TimelineLane.onOff(
          label: 'Ped',
          height: 30,
          spans: [
            LaneSpan(0.5, 1.5),
            LaneSpan(2, 3, level: 0.5),
            LaneSpan(8, 9)
          ],
          start: 0,
          span: 4,
        ));
    final m = await hoverAt(t, 1, dy: 15);
    expect(find.text('on'), findsOneWidget);
    await m.moveTo(const Offset(44 + 250, 15));
    await t.pump();
    expect(find.text('50%'), findsOneWidget);
    await m.moveTo(const Offset(44 + 180, 15));
    await t.pump();
    expect(find.text('off'), findsOneWidget);
    await m.removePointer();
  });

  testWidgets('stalk lane picks the tallest stalk near the pointer', (t) async {
    await pump(
        t,
        const TimelineLane.stalks(
          label: 'Vel',
          points: [
            LanePoint(1, 40),
            LanePoint(1.01, 100, color: Color(0xFFFF0000)),
            LanePoint(3, 64),
            LanePoint(7, 1),
          ],
          start: 0,
          span: 4,
        ));
    final m = await hoverAt(t, 1);
    expect(find.text('100'), findsOneWidget);
    await m.moveTo(const Offset(44 + 200, 20));
    await t.pump();
    expect(find.text('64'), findsNothing);
    await m.removePointer();
  });

  testWidgets('flat and empty data paint without error', (t) async {
    await pump(
        t,
        const Column(children: [
          TimelineLane.step(
              label: 'A',
              points: [LanePoint(0, 5), LanePoint(1, 5)],
              start: 0,
              span: 4),
          TimelineLane.step(label: 'B', points: [], start: 0, span: 4),
          TimelineLane.stalks(
              label: 'C',
              points: [LanePoint(1, 0.5)],
              start: 0,
              span: 4,
              format: null),
        ]));
    final m = await hoverAt(t, 1, dy: 100);
    expect(find.text('0.50'), findsOneWidget);
    await m.removePointer();
    expect(t.takeException(), isNull);
  });

  testWidgets('drag and scroll report pans and zooms', (t) async {
    final pans = <double>[];
    final zooms = <(double, double)>[];
    await pump(
        t,
        TimelineLane.step(
          label: 'CC1',
          points: const [LanePoint(0, 1)],
          start: 0,
          span: 4,
          onPanTime: pans.add,
          onZoomTime: (f, a) => zooms.add((f, a)),
        ));
    await t.dragFrom(const Offset(244, 20), const Offset(-100, 0));
    expect(pans.fold<double>(0, (s, d) => s + d), closeTo(1, 0.05));

    pans.clear();
    const at = Offset(244, 20);
    await t.sendEventToBinding(
        const PointerScrollEvent(position: at, scrollDelta: Offset(50, 0)));
    expect(pans.single, closeTo(0.5, 1e-9));
    await t.sendEventToBinding(
        const PointerScrollEvent(position: at, scrollDelta: Offset(0, 30)));
    expect(pans.length, 1); // plain vertical scroll passes through

    await t.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
    await t.sendEventToBinding(
        const PointerScrollEvent(position: at, scrollDelta: Offset(0, 100)));
    await t.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
    expect(pans.last, closeTo(1, 1e-9));

    await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await t.sendEventToBinding(
        const PointerScrollEvent(position: at, scrollDelta: Offset(0, -100)));
    await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    expect(zooms.single.$1, lessThan(1));
    expect(zooms.single.$2, closeTo(2, 1e-9));

    await t.sendEventToBinding(PointerScaleEvent(position: at, scale: 2));
    expect(zooms.length, 1);
  });
}
