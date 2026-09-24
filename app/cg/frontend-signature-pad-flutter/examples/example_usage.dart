import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signature_pad/signature_pad.dart';

/// A settings page draws a signature once; a document later shows the
/// block it was signed with, built only from what the document stored
/// ("Name, Title", the filed PNG, the time). Runs under flutter_test
/// because Flutter UI cannot execute on the bare VM.
void main() {
  testWidgets('sign once, file the mark, stamp a document', (t) async {
    const style = SignatureStyle(
      ink: Color(0xFF14213D),
      rule: Color(0xFF8A8A8A),
      handFontFamily: null, // a consumer bundles a script font and names it here
      labelStyle: TextStyle(fontSize: 11, letterSpacing: 1.5, color: Color(0xFF6B6B6B)),
    );
    SignatureStrokes strokes = const [];

    // 1. The pad on a settings page.
    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: SizedBox(
          width: 320,
          child: StatefulBuilder(
            builder: (context, set) => SignaturePad(
              strokes: strokes,
              style: style,
              onChanged: (s) => set(() => strokes = s),
            ),
          ),
        ),
      ),
    ));
    final pad = t.getTopLeft(find.byType(GestureDetector).first);
    final g = await t.startGesture(pad + const Offset(40, 60));
    await g.moveBy(const Offset(60, -30));
    await g.moveBy(const Offset(60, 40));
    await g.up();
    await t.pump();
    expect(strokes, hasLength(1));

    // 2. Filing: render what was drawn to a PNG, keep it beside the document.
    final png = await t.runAsync(() => signatureStrokesToPng(strokes, color: style.ink));
    expect(png, isNotNull);

    // 3. The document, later, from stored fields only.
    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: SignatureBlock.stored(
          label: 'ACCEPTED AND SIGNED',
          signedBy: 'A. Person, Reviewer',
          signedAt: DateTime(2026, 3, 14),
          image: null, // the filed PNG's path in a real consumer
          style: style,
        ),
      ),
    ));
    expect(find.text('ACCEPTED AND SIGNED'), findsOneWidget);
    expect(find.text('Reviewer · 14 March 2026'), findsOneWidget);
  });
}
