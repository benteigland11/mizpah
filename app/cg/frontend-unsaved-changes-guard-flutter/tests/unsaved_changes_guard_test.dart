import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:unsaved_changes_guard/unsaved_changes_guard.dart';

void main() {
  group('guardUnsaved', () {
    late int saves, discards, asks;
    setUp(() => saves = discards = asks = 0);

    Future<bool> run(bool dirty, LeaveChoice? choice, {bool saveOk = true}) =>
        guardUnsaved(
          dirty: dirty,
          ask: () async { asks++; return choice; },
          save: () async { saves++; return saveOk; },
          discard: () => discards++,
        );

    test('clean: proceeds without asking', () async {
      expect(await run(false, LeaveChoice.stay), isTrue);
      expect(asks, 0);
    });

    test('save: saves then proceeds', () async {
      expect(await run(true, LeaveChoice.save), isTrue);
      expect(saves, 1);
      expect(discards, 0);
    });

    test('save that fails: stays', () async {
      expect(await run(true, LeaveChoice.save, saveOk: false), isFalse);
    });

    test('discard: discards then proceeds', () async {
      expect(await run(true, LeaveChoice.discard), isTrue);
      expect(discards, 1);
      expect(saves, 0);
    });

    test('stay or dismissed: stays, touches nothing', () async {
      expect(await run(true, LeaveChoice.stay), isFalse);
      expect(await run(true, null), isFalse);
      expect(saves + discards, 0);
    });
  });

  group('UnsavedChangesPrompt', () {
    testWidgets('names the subject and reports each choice', (t) async {
      final choices = <LeaveChoice>[];
      await t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: UnsavedChangesPrompt(subject: 'Plan A', onChoice: choices.add),
        ),
      ));
      expect(find.textContaining('Plan A has edits'), findsOneWidget);
      await t.tap(find.text('SAVE AND CONTINUE'));
      await t.tap(find.text('DISCARD'));
      await t.tap(find.text('KEEP EDITING'));
      expect(choices,
          [LeaveChoice.save, LeaveChoice.discard, LeaveChoice.stay]);
    });

    testWidgets('labels and styles are overridable', (t) async {
      await t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: UnsavedChangesPrompt(
            subject: 'X',
            onChoice: (_) {},
            saveLabel: 'Yes',
            discardLabel: 'No',
            stayLabel: 'Wait',
            style: const PromptStyle(
              dangerButton: ButtonLook(foreground: Color(0xFF123456)),
            ),
          ),
        ),
      ));
      expect(find.text('Yes'), findsOneWidget);
      expect(find.text('Wait'), findsOneWidget);
      expect(t.widget<Text>(find.text('No')).style?.color,
          const Color(0xFF123456));
    });
  });
}
