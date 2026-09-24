import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:unsaved_changes_guard/unsaved_changes_guard.dart';

/// Wiring the guard to a screen switch. The prompt is hosted however the
/// app likes (a dialog, an overlay); here it's driven directly.
void main() {
  testWidgets('guarded navigation', (t) async {
    var dirty = true;
    var saved = false;
    LeaveChoice? pending;

    Future<LeaveChoice?> ask() async {
      // In an app: showDialog(builder: (_) => UnsavedChangesPrompt(...)).
      await t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: UnsavedChangesPrompt(
            subject: 'Document',
            onChoice: (c) => pending = c,
          ),
        ),
      ));
      await t.tap(find.text('SAVE AND CONTINUE'));
      return pending;
    }

    final proceed = await guardUnsaved(
      dirty: dirty,
      ask: ask,
      save: () async {
        saved = true;
        dirty = false;
        return true;
      },
      discard: () => dirty = false,
    );

    expect(proceed, isTrue);
    expect(saved, isTrue);
    expect(dirty, isFalse);
  });
}
