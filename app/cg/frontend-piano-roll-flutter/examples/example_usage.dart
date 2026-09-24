import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:piano_roll/piano_roll.dart';

/// Two bars of a melody over a bass line in beats, with a bar ruler, then
/// a zoom into the first bar the way ctrl+scroll would.
void main() {
  testWidgets('a melody and bass on a roll', (tester) async {
    const melody = [60, 62, 64, 65, 67, 65, 64, 62];
    final notes = [
      for (var i = 0; i < melody.length; i++)
        PianoRollNote(start: i.toDouble(), end: i + 0.9, pitch: melody[i], velocity: 0.5 + i / 16),
      const PianoRollNote(start: 0, end: 4, pitch: 48, part: 1),
      const PianoRollNote(start: 4, end: 8, pitch: 43, part: 1),
    ];
    final viewport = PianoRollViewport(timeMax: 8, pitchMin: 40, pitchMax: 72);

    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(
        width: 600,
        height: 300,
        child: PianoRoll(
          notes: notes,
          viewport: viewport,
          grid: [
            for (var beat = 0; beat <= 8; beat++)
              PianoRollGridLine(beat.toDouble(),
                  bar: beat % 4 == 0, label: beat % 4 == 0 ? 'bar ${beat ~/ 4 + 1}' : null),
          ],
          describe: (n) => 'pitch ${n.pitch}, beat ${n.start.toInt() + 1}',
        ),
      ),
    ));

    viewport.zoomTime(0.5, anchor: 0);
    await tester.pump();
    expect((viewport.start, viewport.end), (0.0, 4.0));
  });
}
