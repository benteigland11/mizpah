import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:inline_editable_text/inline_editable_text.dart';

/// Host that owns the value the way a real caller would.
class _Host extends StatefulWidget {
  const _Host({
    this.initial = 'hello',
    this.placeholder = '',
    this.autofocus = false,
    this.indicator,
  });
  final String initial;
  final String placeholder;
  final bool autofocus;
  final Widget? indicator;

  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> {
  late String value = widget.initial;
  int resetKey = 0;
  final log = <String>[];

  /// What a caller does on load/revert: replace the value, bump the key.
  void reload(String v) => setState(() {
        value = v;
        resetKey++;
      });

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.ltr,
      child: Column(
        children: [
          InlineEditableText(
            value: value,
            resetKey: resetKey,
            placeholder: widget.placeholder,
            autofocus: widget.autofocus,
            editIndicator: widget.indicator,
            onChanged: (v) {
              log.add(v);
              setState(() => value = v);
            },
          ),
          // Somewhere else to send focus.
          const Text('outside'),
        ],
      ),
    );
  }
}

_HostState host(WidgetTester t) => t.state(find.byType(_Host));

void main() {
  testWidgets('shows value in view mode, no editor', (t) async {
    await t.pumpWidget(const _Host());
    expect(find.text('hello'), findsOneWidget);
    expect(find.byType(EditableText), findsNothing);
  });

  testWidgets('shows placeholder when empty', (t) async {
    await t.pumpWidget(const _Host(initial: '', placeholder: 'type here'));
    expect(find.text('type here'), findsOneWidget);
  });

  testWidgets('tap enters edit mode and typing reports every change',
      (t) async {
    await t.pumpWidget(const _Host());
    await t.tap(find.text('hello'));
    await t.pump();
    expect(find.byType(EditableText), findsOneWidget);
    await t.enterText(find.byType(EditableText), 'hello world');
    expect(host(t).value, 'hello world');
    expect(host(t).log, ['hello world']);
  });

  testWidgets('Enter commits and leaves edit mode', (t) async {
    await t.pumpWidget(const _Host());
    await t.tap(find.text('hello'));
    await t.pump();
    await t.enterText(find.byType(EditableText), 'changed');
    await t.sendKeyEvent(LogicalKeyboardKey.enter);
    await t.pump();
    expect(find.byType(EditableText), findsNothing);
    expect(host(t).value, 'changed');
  });

  testWidgets('Shift+Enter inserts a newline at the caret', (t) async {
    await t.pumpWidget(const _Host(initial: 'ab'));
    await t.tap(find.text('ab'));
    await t.pump();
    final editable = t.widget<EditableText>(find.byType(EditableText));
    editable.controller.selection = const TextSelection.collapsed(offset: 1);
    await t.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
    await t.sendKeyEvent(LogicalKeyboardKey.enter);
    await t.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
    await t.pump();
    expect(host(t).value, 'a\nb');
    expect(find.byType(EditableText), findsOneWidget);
    expect(editable.controller.selection.baseOffset, 2);
  });

  testWidgets('Escape restores the starting value', (t) async {
    await t.pumpWidget(const _Host());
    await t.tap(find.text('hello'));
    await t.pump();
    await t.enterText(find.byType(EditableText), 'oops');
    expect(host(t).value, 'oops');
    await t.sendKeyEvent(LogicalKeyboardKey.escape);
    await t.pump();
    expect(host(t).value, 'hello');
    expect(find.byType(EditableText), findsNothing);
  });

  testWidgets('resetKey resynchronises the editor text', (t) async {
    await t.pumpWidget(const _Host());
    await t.tap(find.text('hello'));
    await t.pump();
    await t.enterText(find.byType(EditableText), 'typing');
    host(t).reload('reloaded');
    await t.pump();
    final editable = t.widget<EditableText>(find.byType(EditableText));
    expect(editable.controller.text, 'reloaded');
  });

  testWidgets('autofocus opens directly in edit mode', (t) async {
    await t.pumpWidget(const _Host(autofocus: true));
    await t.pump();
    expect(find.byType(EditableText), findsOneWidget);
  });

  testWidgets('edit indicator appears on hover in view mode only',
      (t) async {
    await t.pumpWidget(const _Host(indicator: Text('✎')));
    expect(find.text('✎'), findsNothing);
    final g = await t.createGesture(kind: PointerDeviceKind.mouse);
    await g.addPointer(location: Offset.zero);
    addTearDown(g.removePointer);
    await g.moveTo(t.getCenter(find.text('hello')));
    await t.pump();
    expect(find.text('✎'), findsOneWidget);
    await t.tap(find.text('hello'));
    await t.pump();
    expect(find.text('✎'), findsNothing);
  });

  testWidgets('click-drag selects text while editing', (t) async {
    await t.pumpWidget(const _Host(initial: 'hello world'));
    await t.tap(find.text('hello world'));
    await t.pump();
    final editable = find.byType(EditableText);
    final left = t.getTopLeft(editable) + const Offset(2, 8);
    final right = t.getTopRight(editable) + const Offset(-2, 8);
    final g = await t.startGesture(left, kind: PointerDeviceKind.mouse);
    await g.moveTo(right);
    await g.up();
    await t.pump();
    final sel = t.widget<EditableText>(editable).controller.selection;
    expect(sel.isCollapsed, isFalse);
    expect(sel.end - sel.start, greaterThan(3));
  });

  testWidgets('box keeps its size between view and edit', (t) async {
    await t.pumpWidget(const _Host());
    final before = t.getSize(find.byType(InlineEditableText));
    await t.tap(find.text('hello'));
    await t.pump();
    final after = t.getSize(find.byType(InlineEditableText));
    expect(after.height, before.height);
    expect(after.width, before.width);
  });

  testWidgets('onCommit fires once on Enter with the final text, not per keystroke', (tester) async {
    final commits = <String>[];
    var changes = 0;
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: MediaQuery(
        data: const MediaQueryData(),
        child: InlineEditableText(
          value: 'a',
          onChanged: (_) => changes++,
          onCommit: commits.add,
          autofocus: true,
        ),
      ),
    ));
    await tester.enterText(find.byType(EditableText), 'abc');
    await tester.pump();
    expect(commits, isEmpty);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(commits, ['abc']);
    expect(changes, greaterThan(0));
  });

  testWidgets('onCommit does not fire after Escape', (tester) async {
    final commits = <String>[];
    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: MediaQuery(
        data: const MediaQueryData(),
        child: InlineEditableText(value: 'a', onChanged: (_) {}, onCommit: commits.add, autofocus: true),
      ),
    ));
    await tester.enterText(find.byType(EditableText), 'abc');
    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    await tester.pumpAndSettle();
    expect(commits, isEmpty);
  });
}
