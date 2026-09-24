import 'package:flutter/material.dart' show SelectableText;
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:syntax_highlighted_code/syntax_highlighted_code.dart';

const pal = HighlightPalette(
  keyword: Color(0xFF000001),
  string: Color(0xFF000002),
  number: Color(0xFF000003),
  literal: Color(0xFF000004),
  comment: Color(0xFF000005),
  key: Color(0xFF000006),
  title: Color(0xFF000007),
  meta: Color(0xFF000008),
  punctuation: Color(0xFF000009),
);

/// The colour each leaf piece of text is drawn in, e.g. {'"name"': key}.
Map<String, Color?> colours(TextSpan root) {
  final out = <String, Color?>{};
  void walk(InlineSpan s, Color? inherited) {
    if (s is! TextSpan) return;
    final c = s.style?.color ?? inherited;
    final t = s.text;
    if (t != null && t.trim().isNotEmpty) out[t.trim()] = c;
    for (final k in s.children ?? const <InlineSpan>[]) {
      walk(k, c);
    }
  }

  walk(root, null);
  return out;
}

void main() {
  test('languageForPath by extension and name', () {
    expect(languageForPath('probe/measure.py'), 'python');
    expect(languageForPath(r'data\BASE.JSON'), 'json');
    expect(languageForPath('config.toml'), 'ini');
    expect(languageForPath('score.svg'), 'xml');
    expect(languageForPath('Makefile'), 'makefile');
    expect(languageForPath('.zshrc'), 'bash');
    expect(languageForPath('piece.mid'), isNull);
    expect(languageForPath('README'), isNull);
    expect(highlightLanguages, containsAll(['json', 'python', 'bash', 'yaml', 'markdown']));
  });

  test('guessLanguage', () {
    expect(guessLanguage('  {"a": 1}'), 'json');
    expect(guessLanguage('[1, 2]'), 'json');
    expect(guessLanguage('{not json'), isNull);
    expect(guessLanguage('#!/usr/bin/env python3\nprint(1)'), 'python');
    expect(guessLanguage('#!/bin/bash\necho hi'), 'bash');
    expect(guessLanguage('#!/usr/bin/env node'), isNull);
    expect(guessLanguage('plain words'), isNull);
    expect(guessLanguage('"just a string"'), isNull);
  });

  test('JSON: keys, strings, numbers and literals take their roles', () {
    final c = colours(highlightCode('{"name": "piano", "n": 8, "ok": true}', language: 'json', palette: pal));
    expect(c['"name"'], pal.key);
    expect(c['"piano"'], pal.string);
    expect(c['8'], pal.number);
    expect(c['true'], pal.keyword); // highlight.js scopes JSON literals as keywords
  });

  test('python: keyword, comment, title', () {
    final c = colours(highlightCode('def f(x):\n    # note\n    return "s"', language: 'python', palette: pal));
    expect(c['def'], pal.keyword);
    expect(c['return'], pal.keyword);
    expect(c['# note'], pal.comment);
    expect(c['f'], pal.title);
    expect(c['"s"'], pal.string);
    final k = colours(highlightCode('class A(B): pass', language: 'python', palette: pal));
    expect(k['A'], pal.title);
    expect(k['B'], pal.title);
    final b = colours(highlightCode(r'ls -la $HOME', language: 'bash', palette: pal));
    expect(b['ls'], pal.title);
    expect(b[r'$HOME'], pal.key);
  });

  test('plain fallbacks: no language, unknown language, too long', () {
    const style = TextStyle(fontSize: 11);
    for (final span in [
      highlightCode('x = 1', style: style),
      highlightCode('x = 1', language: 'cobol', style: style),
      highlightCode('{"a": 1}', language: 'json', style: style, maxLength: 3),
    ]) {
      expect(span.children, isNull);
      expect(span.style, style);
    }
    // Markdown with embedded HTML (a sub-language) still colours.
    expect(highlightCode('# Title\n<b>x</b>', language: 'markdown').children, isNotEmpty);
    expect(highlightCode('<div><style>a{}</style><script>x()</script></div>', language: 'xml').children, isNotEmpty);
  });

  test('palette: null roles are skipped; equality; italic toggle', () {
    const base = TextStyle(fontSize: 10);
    final t = const HighlightPalette(keyword: null, italicComments: false, comment: null).theme(base);
    expect(t.containsKey('keyword'), isFalse);
    expect(t.containsKey('comment'), isFalse);
    expect(t['strong']!.fontWeight, FontWeight.w700);
    expect(const HighlightPalette().theme(base)['comment']!.fontStyle, FontStyle.italic);
    expect(const HighlightPalette(), const HighlightPalette());
    expect(const HighlightPalette().hashCode, const HighlightPalette().hashCode);
    expect(pal == const HighlightPalette(), isFalse);
  });

  Future<void> host(WidgetTester t, Widget w) => t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Align(alignment: Alignment.topLeft, child: SizedBox(width: 300, child: w)),
      ));

  testWidgets('line numbers, wrapping, selection and updates', (t) async {
    await host(t, const HighlightedCode(text: 'a\nb\nc', language: 'python', lineNumbers: true));
    expect(find.text('1\n2\n3'), findsOneWidget);
    expect(find.byType(SingleChildScrollView), findsOneWidget);

    await host(t, const HighlightedCode(text: 'x = 1', language: 'python'));
    expect(find.text('1'), findsNothing);
    expect(find.byType(SingleChildScrollView), findsNothing);
    expect(find.byType(SelectableText), findsOneWidget);

    await host(t, const HighlightedCode(text: 'y = 2', selectable: false, softWrap: false));
    expect(find.byType(SelectableText), findsNothing);
    expect(find.byType(RichText), findsWidgets);
    expect(find.byType(SingleChildScrollView), findsOneWidget);

    // A changed text re-colours.
    await host(t, const HighlightedCode(text: 'z = 3', selectable: false, softWrap: false));
    expect(find.textContaining('z = 3', findRichText: true), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
