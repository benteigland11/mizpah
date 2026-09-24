import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ops_typography_kit/ops_typography_kit.dart';

Widget host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 600, child: child)),
    );

void main() {
  group('SectionLabel', () {
    testWidgets('uppercases and draws a rule', (t) async {
      await t.pumpWidget(host(const SectionLabel('Needs')));
      expect(find.text('NEEDS'), findsOneWidget);
      final rule = t.widgetList<Container>(find.byType(Container)).last;
      expect(rule.constraints?.maxHeight ?? 1, 1);
    });

    testWidgets('left aligned by default, trailing at the right', (t) async {
      await t.pumpWidget(
          host(const SectionLabel('Needs', trailing: Text('3 OPEN'))));
      final heading = t.getTopLeft(find.text('NEEDS'));
      final trailing = t.getTopRight(find.text('3 OPEN'));
      final box = t.getRect(find.byType(SectionLabel));
      expect(heading.dx, box.left);
      expect(trailing.dx, box.right);
    });

    testWidgets('centered puts the heading mid-line, trailing stays right',
        (t) async {
      await t.pumpWidget(host(const SectionLabel('Needs',
          centered: true, trailing: Text('3 OPEN'))));
      final heading = t.getCenter(find.text('NEEDS'));
      final box = t.getRect(find.byType(SectionLabel));
      expect(heading.dx, closeTo(box.center.dx, 1));
      expect(t.getTopRight(find.text('3 OPEN')).dx, box.right);
    });
  });

  group('KeyValueStat', () {
    testWidgets('label uppercased, value slot has fixed height', (t) async {
      await t.pumpWidget(host(const KeyValueStat('version', Text('v2'),
          valueHeight: 40)));
      expect(find.text('VERSION'), findsOneWidget);
      final slot = find.ancestor(
          of: find.text('v2'), matching: find.byType(SizedBox)).first;
      expect(t.getSize(slot).height, 40);
    });

    testWidgets('accent colours the value; onTap makes it tappable',
        (t) async {
      var taps = 0;
      const style = OpsStyle(accentColor: Color(0xFF123456));
      await t.pumpWidget(host(KeyValueStat('open', const Text('2'),
          style: style, accent: true, onTap: () => taps++)));
      final dts = t.widget<DefaultTextStyle>(find.ancestor(
          of: find.text('2'), matching: find.byType(DefaultTextStyle)).first);
      expect(dts.style.color, const Color(0xFF123456));
      await t.tap(find.text('2'));
      expect(taps, 1);
    });
  });

  group('StatStrip', () {
    testWidgets('puts a divider between cells and trailing at the right',
        (t) async {
      await t.pumpWidget(host(const StatStrip(
        trailing: Text('T'),
        children: [Text('A'), Text('B'), Text('C')],
      )));
      // Dividers are the 1px-wide containers: one fewer than the cells.
      final dividers = t
          .widgetList<Container>(find.byType(Container))
          .where((c) => c.constraints?.maxWidth == 1)
          .length;
      expect(dividers, 2);
      final box = t.getRect(find.byType(StatStrip));
      expect(t.getTopRight(find.text('T')).dx, closeTo(box.right - 8, 0.5));
    });

    testWidgets('header sits above the cells', (t) async {
      await t.pumpWidget(host(const StatStrip(
        header: Text('H'),
        children: [Text('A')],
      )));
      expect(t.getBottomLeft(find.text('H')).dy,
          lessThanOrEqualTo(t.getTopLeft(find.text('A')).dy));
    });

    testWidgets('cells wrap instead of overflowing beside trailing',
        (t) async {
      await t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: SizedBox(
            width: 300,
            child: StatStrip(
              trailing: const SizedBox(width: 120, height: 40),
              children: [
                for (var i = 0; i < 4; i++)
                  const SizedBox(width: 90, height: 30),
              ],
            ),
          ),
        ),
      ));
      // No overflow exception, and the strip grew to hold a second line.
      expect(t.takeException(), isNull);
      expect(t.getSize(find.byType(StatStrip)).height, greaterThan(60));
    });
  });

  group('StatusBadge', () {
    testWidgets('uppercases; hot uses accent colour', (t) async {
      const style = OpsStyle(
          accentColor: Color(0xFF00FF00), textColor: Color(0xFF0000FF));
      await t.pumpWidget(host(const Row(children: [
        StatusBadge('draft', style: style),
        StatusBadge('active', style: style, hot: true),
      ])));
      expect(t.widget<Text>(find.text('DRAFT')).style?.color,
          const Color(0xFF0000FF));
      expect(t.widget<Text>(find.text('ACTIVE')).style?.color,
          const Color(0xFF00FF00));
    });
  });

  group('RowIndex', () {
    testWidgets('zero-pads and is one-based by default', (t) async {
      await t.pumpWidget(host(const Row(children: [
        RowIndex(0),
        RowIndex(9),
        RowIndex(3, oneBased: false, digits: 3),
      ])));
      expect(find.text('01'), findsOneWidget);
      expect(find.text('10'), findsOneWidget);
      expect(find.text('003'), findsOneWidget);
    });
  });
}
