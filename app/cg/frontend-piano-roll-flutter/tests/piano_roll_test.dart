import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:piano_roll/piano_roll.dart';

const notes = [
  PianoRollNote(start: 0, end: 1, pitch: 60, velocity: 0.9),
  PianoRollNote(start: 1, end: 2, pitch: 64, velocity: 0.2, part: 1),
  PianoRollNote(start: 2, end: 4, pitch: 67, part: 2),
  PianoRollNote(start: 3, end: 3.5, pitch: 61),
];

// 444 x 380: keyboard 44 and ruler 20 leave a 400 x 360 plot.
Future<void> pump(WidgetTester t, Widget roll) => t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 444, height: 380, child: roll)),
    ));

Offset plotPoint(WidgetTester t, double dx, double dy) =>
    t.getTopLeft(find.byType(PianoRoll)) + Offset(44 + dx, 20 + dy);

void main() {
  group('viewport', () {
    test('clamps, pans and zooms inside its bounds', () {
      final v = PianoRollViewport(timeMax: 100, pitchMin: 40, pitchMax: 80);
      expect((v.start, v.end, v.pitchLow, v.pitchHigh), (0, 100, 40, 80));
      var calls = 0;
      v.addListener(() => calls++);
      v.zoomTime(0.5, anchor: 0);
      expect((v.start, v.span), (0, 50));
      v.panTime(80);
      expect(v.start, 50);
      v.zoomTime(0.0000001);
      expect(v.span, closeTo(0.1, 1e-9));
      v.zoomTime(1e9);
      expect((v.start, v.span), (0, 100));
      v.zoomPitch(0.5, anchor: 60);
      expect((v.pitchLow, v.pitchHigh), (50, 70));
      v.panPitch(100);
      expect(v.pitchHigh, 80);
      v.zoomPitch(0.01);
      expect(v.pitchHigh - v.pitchLow, 6);
      v.setPitch(0, 200);
      expect((v.pitchLow, v.pitchHigh), (40, 80));
      v.setTime(10, 20);
      v.fit();
      expect((v.start, v.span), (0, 100));
      expect(v.timeMin, 0);
      expect(v.timeMax, 100);
      expect(v.pitchMin, 40);
      expect(v.pitchMax, 80);
      expect(calls, greaterThan(8));
    });

    test('degenerate bounds stay usable', () {
      final v = PianoRollViewport(timeMin: 5, timeMax: 5, pitchMin: 60, pitchMax: 60, minTimeSpan: 0.5);
      expect(v.span, 1);
      expect(v.pitchHigh - v.pitchLow, 1);
      v.zoomTime(0.01);
      expect(v.span, 0.5);
    });
  });

  testWidgets('paints notes, grid, ruler and keyboard', (t) async {
    final v = PianoRollViewport(timeMax: 4, pitchMin: 58, pitchMax: 70);
    await pump(t, PianoRoll(
      notes: notes,
      viewport: v,
      grid: [
        for (var b = 0; b <= 16; b++)
          PianoRollGridLine(b / 4, bar: b % 4 == 0, label: b % 4 == 0 ? '${b ~/ 4 + 1}' : null),
      ],
    ));
    expect(find.byType(CustomPaint), findsWidgets);
    // Zoom far out so beat lines thin away; zoom in so labels crowd.
    v.zoomTime(10);
    await t.pump();
    v.zoomTime(0.001);
    await t.pump();
    v.zoomPitch(10);
    await t.pump();
    expect(t.takeException(), isNull);
  });

  testWidgets('hover shows the described note and leaves on exit', (t) async {
    final v = PianoRollViewport(timeMax: 4, pitchMin: 58, pitchMax: 70);
    await pump(t, PianoRoll(
      notes: notes,
      viewport: v,
      describe: (n) => 'pitch ${n.pitch}',
    ));
    // 12 rows of 30px; pitch 60 is row 9 from the top (y 270-300); t 0-1 is x 0-100.
    final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: Offset.zero);
    await mouse.moveTo(plotPoint(t, 50, 285));
    await t.pump();
    expect(find.text('pitch 60'), findsOneWidget);
    await mouse.moveTo(plotPoint(t, 350, 285)); // right side: card flips left
    await t.pump();
    expect(find.text('pitch 60'), findsNothing);
    await mouse.moveTo(plotPoint(t, 350, 75)); // pitch 67 (y 60-90) at t 3.5
    await t.pump();
    expect(find.text('pitch 67'), findsOneWidget);
    await mouse.moveTo(const Offset(1, 1));
    await t.pump();
    expect(find.textContaining('pitch'), findsNothing);
    await mouse.removePointer();
  });

  testWidgets('scroll pans, modifiers zoom, drag pans, double tap fits', (t) async {
    final v = PianoRollViewport(timeMax: 4, pitchMin: 40, pitchMax: 80);
    v.setPitch(50, 62);
    await pump(t, PianoRoll(notes: notes, viewport: v));
    final at = plotPoint(t, 200, 180);

    await t.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: const Offset(0, 30)));
    expect(v.pitchLow, lessThan(50));

    await t.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: const Offset(40, 0)));
    expect(v.start, 0); // cannot pan: whole range shown

    await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await t.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: const Offset(0, -200)));
    await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    expect(v.span, lessThan(4));
    final zoomed = v.span;

    await t.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
    await t.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: const Offset(0, 50)));
    await t.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
    expect(v.start, greaterThan(0));
    expect(v.span, zoomed);

    await t.sendKeyDownEvent(LogicalKeyboardKey.altLeft);
    await t.sendEventToBinding(PointerScrollEvent(position: at, scrollDelta: const Offset(0, 100)));
    await t.sendKeyUpEvent(LogicalKeyboardKey.altLeft);
    expect(v.pitchHigh - v.pitchLow, greaterThan(12));

    await t.sendEventToBinding(PointerScaleEvent(position: at, scale: 2));
    expect(v.span, lessThan(zoomed));

    final before = v.start;
    await t.dragFrom(at, const Offset(60, 0));
    expect(v.start, lessThan(before));

    await t.tapAt(at);
    await t.pump(const Duration(milliseconds: 50));
    await t.tapAt(at);
    await t.pumpAndSettle();
    expect((v.start, v.span, v.pitchLow, v.pitchHigh), (0, 4, 40, 80));
  });

  testWidgets('pinch zooms time', (t) async {
    final v = PianoRollViewport(timeMax: 4);
    await pump(t, PianoRoll(notes: notes, viewport: v));
    final c = plotPoint(t, 200, 180);
    final a = await t.startGesture(c - const Offset(20, 0));
    final b = await t.startGesture(c + const Offset(20, 0));
    await a.moveTo(c - const Offset(80, 0));
    await b.moveTo(c + const Offset(80, 0));
    await t.pump();
    await a.up();
    await b.up();
    await t.pump(const Duration(seconds: 1));
    expect(v.span, lessThan(4));
  });

  testWidgets('drum mode rows only the pitches present, labelled', (t) async {
    final v = PianoRollViewport(timeMax: 4);
    const hits = [
      PianoRollNote(start: 0, end: 0.1, pitch: 36),
      PianoRollNote(start: 1, end: 1.1, pitch: 38, part: 1),
      PianoRollNote(start: 1, end: 1.1, pitch: 42, velocity: 0.3),
    ];
    await pump(t, PianoRoll(
      notes: hits,
      viewport: v,
      drums: true,
      rowLabel: (p) => {36: 'Kick', 38: 'Snare', 42: 'Hat'}[p],
      describe: (n) => 'hit ${n.pitch}',
    ));
    // Three rows of 120px, highest pitch on top: 42, 38, 36.
    final mouse = await t.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: Offset.zero);
    await mouse.moveTo(plotPoint(t, 100, 180)); // t = 1, row 38
    await t.pump();
    expect(find.text('hit 38'), findsOneWidget);
    await mouse.moveTo(plotPoint(t, 100, 60)); // row 42 hovered
    await t.pump();
    expect(find.text('hit 42'), findsOneWidget);
    await mouse.moveTo(plotPoint(t, 100, 400)); // below the rows
    await t.pump();
    expect(find.textContaining('hit'), findsNothing);
    await mouse.removePointer();

    // Vertical scroll does nothing to pitch in drum mode.
    final low = v.pitchLow;
    await t.sendEventToBinding(PointerScrollEvent(position: plotPoint(t, 10, 10), scrollDelta: const Offset(0, 30)));
    expect(v.pitchLow, low);

    // Swapping notes re-indexes; default labels and an empty part palette.
    await pump(t, PianoRoll(
      notes: const [PianoRollNote(start: 0, end: 1, pitch: 50)],
      viewport: v,
      drums: true,
      style: const PianoRollStyle(partColors: []),
    ));
    await pump(t, PianoRoll(notes: const [], viewport: v, drums: true));
    expect(t.takeException(), isNull);
  });

  test('style colours cycle by part', () {
    const s = PianoRollStyle(partColors: [Color(0xFF000001), Color(0xFF000002)]);
    expect(s.colorOf(3), const Color(0xFF000002));
    expect(const PianoRollStyle(partColors: []).colorOf(0), const Color(0xFFFFFFFF));
  });
}
