import 'dart:io';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signature_pad/signature_pad.dart';

Widget _wrap(Widget child, {double width = 320}) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: width, child: child)),
    );

const SignatureStrokes _hand = [
  [[0.1, 0.5], [0.3, 0.2], [0.5, 0.8]],
  [[0.6, 0.5], [0.9, 0.5]],
];

void main() {
  group('SignaturePad', () {
    testWidgets('drag lays down a stroke, normalised to the pad, reported on lift', (t) async {
      SignatureStrokes? got;
      await t.pumpWidget(_wrap(SignaturePad(strokes: const [], onChanged: (s) => got = s)));
      expect(find.text('sign here'), findsOneWidget);
      final pad = find.byType(GestureDetector).first;
      final tl = t.getTopLeft(pad);
      final g = await t.startGesture(tl + const Offset(32, 50));
      await g.moveBy(const Offset(64, 0));
      await g.moveBy(const Offset(64, 0));
      await g.up();
      await t.pump();
      expect(got, isNotNull);
      expect(got!.length, 1);
      expect(got!.first.length, 3);
      expect(got!.first.first[0], closeTo(0.1, 0.01)); // 32 / 320
      expect(got!.first.last[0], closeTo(0.5, 0.01)); // 160 / 320
      expect(got!.first.first[1], closeTo(0.5, 0.01)); // 50 / 100
      expect(find.text('sign here'), findsNothing);
    });

    testWidgets('points are clamped to 0..1 and clear reports an empty list', (t) async {
      SignatureStrokes? got;
      await t.pumpWidget(_wrap(SignaturePad(strokes: const [], onChanged: (s) => got = s)));
      final pad = find.byType(GestureDetector).first;
      final g = await t.startGesture(t.getTopLeft(pad) + const Offset(10, 10));
      await g.moveBy(const Offset(-200, -200));
      await g.up();
      await t.pump();
      expect(got!.first.last, [0.0, 0.0]);
      await t.tap(find.text('CLEAR'));
      await t.pump();
      expect(got, isEmpty);
      expect(find.text('sign here'), findsOneWidget);
    });

    testWidgets('starts from given strokes; a caller clearing them empties the pad', (t) async {
      var strokes = _hand;
      late StateSetter set;
      await t.pumpWidget(_wrap(StatefulBuilder(
        builder: (context, s) {
          set = s;
          return SignaturePad(strokes: strokes, onChanged: (v) => strokes = v);
        },
      )));
      expect(find.text('sign here'), findsNothing);
      set(() => strokes = const []);
      await t.pump();
      expect(find.text('sign here'), findsOneWidget);
    });

    testWidgets('style strings and pad height follow the aspect', (t) async {
      await t.pumpWidget(_wrap(
        SignaturePad(
          strokes: const [],
          onChanged: (_) {},
          aspect: 4,
          style: const SignatureStyle(padHint: 'draw', padHelp: 'help', clearLabel: 'RESET'),
        ),
        width: 400,
      ));
      expect(find.text('draw'), findsOneWidget);
      expect(find.text('help'), findsOneWidget);
      expect(find.text('RESET'), findsOneWidget);
      expect(t.getSize(find.byType(GestureDetector).first).height, 100);
    });
  });

  group('SignatureMark', () {
    testWidgets('name in a hand when there is nothing drawn', (t) async {
      await t.pumpWidget(_wrap(const SignatureMark(
        strokes: [],
        name: 'A. Person',
        style: SignatureStyle(handFontFamily: 'Cursive'),
      )));
      final text = t.widget<Text>(find.text('A. Person'));
      expect(text.style!.fontFamily, 'Cursive');
      expect(find.byType(CustomPaint), findsNothing);
    });

    testWidgets('strokes paint at height x aspect', (t) async {
      await t.pumpWidget(const Directionality(
        textDirection: TextDirection.ltr,
        child: Center(child: SignatureMark(strokes: _hand, name: 'A. Person', height: 40)),
      ));
      expect(find.text('A. Person'), findsNothing);
      final box = t.getSize(find.byType(CustomPaint));
      expect(box.height, 40);
      expect(box.width, closeTo(40 * SignatureStyle.defaultAspect, 0.01));
    });

    testWidgets('an image file on disk wins over strokes; a missing file does not', (t) async {
      final dir = Directory.systemTemp.createTempSync('sig');
      addTearDown(() => dir.deleteSync(recursive: true));
      final bytes = await t.runAsync(() => signatureStrokesToPng(_hand, width: 64));
      final png = File('${dir.path}/m.png')..writeAsBytesSync(bytes!);
      await t.pumpWidget(_wrap(SignatureMark(strokes: _hand, name: 'x', image: png.path)));
      expect(find.byType(Image), findsOneWidget);
      // The load fails on the real event loop; the strokes take its place.
      await t.runAsync(() async {
        await t.pumpWidget(_wrap(SignatureMark(strokes: _hand, name: 'x', image: '${dir.path}/none.png')));
        await Future<void>.delayed(const Duration(milliseconds: 200));
      });
      await t.pump();
      expect(find.byType(CustomPaint), findsOneWidget);
    });
  });

  group('SignatureMark imageProvider', () {
    testWidgets('a consumer may load the image its own way', (t) async {
      String? asked;
      await t.pumpWidget(_wrap(SignatureMark(
        strokes: const [],
        name: 'x',
        image: 'remote/mark.png',
        imageProvider: (p) {
          asked = p;
          return const AssetImage('never-loaded');
        },
      )));
      expect(asked, 'remote/mark.png');
      expect(find.byType(Image), findsOneWidget);
    });
  });

  group('SignatureBlock', () {
    testWidgets('label, name, title and date', (t) async {
      await t.pumpWidget(_wrap(SignatureBlock(
        strokes: const [],
        name: 'A. Person',
        title: 'Reviewer',
        signedAt: DateTime(2026, 3, 14, 12),
        label: 'APPROVED',
      )));
      expect(find.text('APPROVED'), findsOneWidget);
      expect(find.text('A. Person'), findsNWidgets(2)); // the hand and the printed line
      expect(find.text('Reviewer · 14 March 2026'), findsOneWidget);
    });

    testWidgets('stored splits "Name, Title" and tolerates no title or date', (t) async {
      await t.pumpWidget(_wrap(SignatureBlock.stored(signedBy: 'A. Person, Site Lead, Night', signedAt: null)));
      expect(find.text('Site Lead, Night'), findsOneWidget);
      await t.pumpWidget(_wrap(SignatureBlock.stored(signedBy: 'Solo', signedAt: null)));
      expect(find.text(''), findsOneWidget);
      expect(splitSignedBy('Solo'), ('Solo', ''));
      expect(splitSignedBy(' A, B '), ('A', 'B'));
    });

    testWidgets('date format is injectable', (t) async {
      await t.pumpWidget(_wrap(SignatureBlock(
        strokes: const [],
        name: 'n',
        title: 't',
        signedAt: DateTime(2026, 1, 2),
        dateFormat: (d) => '${d.year}',
      )));
      expect(find.text('t · 2026'), findsOneWidget);
    });
  });

  group('signatureStrokesToPng', () {
    // Rasterising needs the real event loop, hence runAsync.
    testWidgets('null for nothing; PNG bytes at the requested width otherwise', (t) async {
      expect(await t.runAsync(() => signatureStrokesToPng(const [])), isNull);
      final bytes = await t.runAsync(() => signatureStrokesToPng(_hand, width: 96));
      expect(bytes, isNotNull);
      expect(bytes!.sublist(0, 8), [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
      // IHDR width is big-endian at bytes 16..20.
      final w = (bytes[16] << 24) | (bytes[17] << 16) | (bytes[18] << 8) | bytes[19];
      expect(w, 96);
      final h = (bytes[20] << 24) | (bytes[21] << 16) | (bytes[22] << 8) | bytes[23];
      expect(h, (96 / SignatureStyle.defaultAspect).toInt());
    });
  });

  test('formatSignatureDate', () {
    expect(formatSignatureDate(DateTime(2026, 12, 1)), '1 December 2026');
  });
}
