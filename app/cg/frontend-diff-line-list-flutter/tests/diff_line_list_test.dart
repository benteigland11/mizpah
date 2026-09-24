import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:diff_line_list/diff_line_list.dart';

Widget host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 500, child: child)),
    );

const style = DiffStyle(
  addedColor: Color(0xFF00FF00),
  removedColor: Color(0xFFFF0000),
  contextColor: Color(0xFF777777),
);

void main() {
  testWidgets('added line: + mark, tint, index, trailing in green', (t) async {
    await t.pumpWidget(host(const DiffLine.added('new item',
        style: style, index: 2, trailing: 'NEEDED')));
    expect(find.text('+'), findsOneWidget);
    expect(find.text('03'), findsOneWidget);
    expect(t.widget<Text>(find.text('+')).style?.color, const Color(0xFF00FF00));
    expect(t.widget<Text>(find.text('NEEDED')).style?.color,
        const Color(0xFF00FF00));
    final box = t.widget<Container>(find.byType(Container));
    expect(box.color, isNotNull);
  });

  testWidgets('removed line: − mark, strikethrough in red', (t) async {
    await t.pumpWidget(host(const DiffLine.removed('old', style: style)));
    expect(find.text('−'), findsOneWidget);
    final s = t.widget<Text>(find.text('old')).style!;
    expect(s.decoration, TextDecoration.lineThrough);
    expect(s.decorationColor, const Color(0xFFFF0000));
  });

  testWidgets('context line: no mark, no tint, dim text', (t) async {
    await t.pumpWidget(host(const DiffLine.context('same', style: style)));
    expect(t.widget<Container>(find.byType(Container)).color, isNull);
    expect(t.widget<Text>(find.text('same')).style?.color,
        const Color(0xFF777777));
  });

  testWidgets('AppendDiff numbers context then the added line', (t) async {
    await t.pumpWidget(host(const AppendDiff(
        existing: ['a', 'b'], added: 'c', style: style)));
    expect(find.text('01'), findsOneWidget);
    expect(find.text('02'), findsOneWidget);
    expect(find.text('03'), findsOneWidget);
    expect(find.text('+'), findsOneWidget);
  });

  testWidgets('AppendDiff on an empty list is a single 01 addition',
      (t) async {
    await t.pumpWidget(host(const AppendDiff(
        existing: [], added: 'first', style: style, indexed: true)));
    expect(find.text('01'), findsOneWidget);
    expect(find.byType(DiffLine), findsOneWidget);
  });

  testWidgets('ReplaceDiff shows removed then added', (t) async {
    await t.pumpWidget(host(const ReplaceDiff(
        before: 'old mission', after: 'new mission', style: style)));
    final removed = t.getTopLeft(find.text('old mission'));
    final added = t.getTopLeft(find.text('new mission'));
    expect(removed.dy < added.dy, isTrue);
    expect(find.text('−'), findsOneWidget);
    expect(find.text('+'), findsOneWidget);
  });
}
