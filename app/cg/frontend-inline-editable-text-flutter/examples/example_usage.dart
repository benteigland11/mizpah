import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:inline_editable_text/inline_editable_text.dart';

/// A list of items whose text can be edited in place. Runs under the
/// flutter_test harness because Flutter UI can't execute on the bare VM.
void main() {
  testWidgets('inline editable item list', (t) async {
    final items = ['first item', 'second item'];
    var generation = 0;

    await t.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: StatefulBuilder(
          builder: (context, setState) => Column(
            children: [
              for (var i = 0; i < items.length; i++)
                InlineEditableText(
                  value: items[i],
                  resetKey: generation,
                  placeholder: 'Describe the item',
                  style: const TextStyle(fontSize: 16),
                  textColor: const Color(0xFF14213D),
                  activeBorderColor: const Color(0xFFC0392B),
                  editIndicator: const Text('✎',
                      style: TextStyle(fontSize: 12, color: Color(0xFF8A8A8A))),
                  onChanged: (v) => setState(() => items[i] = v),
                ),
            ],
          ),
        ),
      ),
    );

    // Tap the first line, retype it, commit with Enter.
    await t.tap(find.text('first item'));
    await t.pump();
    await t.enterText(find.byType(EditableText), 'edited item');
    expect(items.first, 'edited item');

    // A wholesale reload: bump the reset key and the field follows.
    items[0] = 'reloaded item';
    generation++;
    await t.pump();
    expect(find.text('reloaded item'), findsOneWidget);
  });
}
