/// Read-only syntax-coloured code: a language from a path or a guess, a
/// small semantic palette, and a text span or widget out.
///
/// ```dart
/// HighlightedCode(
///   text: source,
///   language: languageForPath('probe/measure.py'),
///   palette: const HighlightPalette(),
///   lineNumbers: true,
/// );
/// ```
///
/// Built on re_highlight (highlight.js grammars in Dart) with a fixed set of
/// common languages registered, so only those grammars ship. The palette
/// names roles (keyword, string, key, …) and maps them onto highlight.js
/// scopes, so a caller chooses colours, not scope names. Text longer than
/// `maxLength`, or a language that fails, falls back to plain text.
library;

import 'dart:convert';

import 'package:flutter/material.dart' show SelectableText;
import 'package:flutter/widgets.dart';
import 'package:re_highlight/languages/bash.dart';
import 'package:re_highlight/languages/c.dart';
import 'package:re_highlight/languages/cpp.dart';
import 'package:re_highlight/languages/css.dart';
import 'package:re_highlight/languages/dart.dart';
import 'package:re_highlight/languages/diff.dart';
import 'package:re_highlight/languages/dockerfile.dart';
import 'package:re_highlight/languages/go.dart';
import 'package:re_highlight/languages/ini.dart';
import 'package:re_highlight/languages/javascript.dart';
import 'package:re_highlight/languages/json.dart';
import 'package:re_highlight/languages/latex.dart';
import 'package:re_highlight/languages/makefile.dart';
import 'package:re_highlight/languages/markdown.dart';
import 'package:re_highlight/languages/python.dart';
import 'package:re_highlight/languages/rust.dart';
import 'package:re_highlight/languages/shell.dart';
import 'package:re_highlight/languages/sql.dart';
import 'package:re_highlight/languages/typescript.dart';
import 'package:re_highlight/languages/xml.dart';
import 'package:re_highlight/languages/yaml.dart';
import 'package:re_highlight/re_highlight.dart';

final Map<String, Mode> _grammars = {
  'bash': langBash,
  'c': langC,
  'cpp': langCpp,
  'css': langCss,
  'dart': langDart,
  'diff': langDiff,
  'dockerfile': langDockerfile,
  'go': langGo,
  'ini': langIni,
  'javascript': langJavascript,
  'json': langJson,
  'latex': langLatex,
  'makefile': langMakefile,
  'markdown': langMarkdown,
  'python': langPython,
  'rust': langRust,
  'shell': langShell,
  'sql': langSql,
  'typescript': langTypescript,
  'xml': langXml,
  'yaml': langYaml,
};

/// Languages this widget can colour, by the names [languageForPath] returns.
List<String> get highlightLanguages => _grammars.keys.toList();

const Map<String, String> _byExtension = {
  'sh': 'bash', 'bash': 'bash', 'zsh': 'bash',
  'c': 'c', 'h': 'c',
  'cc': 'cpp', 'cpp': 'cpp', 'cxx': 'cpp', 'hpp': 'cpp', 'hh': 'cpp', 'ino': 'cpp',
  'css': 'css',
  'dart': 'dart',
  'diff': 'diff', 'patch': 'diff',
  'go': 'go',
  'ini': 'ini', 'toml': 'ini', 'cfg': 'ini', 'conf': 'ini', 'properties': 'ini',
  'js': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript', 'jsx': 'javascript',
  'json': 'json', 'jsonl': 'json', 'ipynb': 'json', 'arb': 'json',
  'tex': 'latex', 'sty': 'latex',
  'md': 'markdown', 'markdown': 'markdown',
  'py': 'python', 'pyi': 'python',
  'rs': 'rust',
  'sql': 'sql',
  'ts': 'typescript', 'tsx': 'typescript',
  'xml': 'xml', 'html': 'xml', 'htm': 'xml', 'svg': 'xml', 'plist': 'xml', 'musicxml': 'xml',
  'yaml': 'yaml', 'yml': 'yaml',
};

const Map<String, String> _byName = {
  'dockerfile': 'dockerfile',
  'makefile': 'makefile',
  'gnumakefile': 'makefile',
  '.bashrc': 'bash',
  '.zshrc': 'bash',
  '.profile': 'bash',
};

/// The language for a file [path] by its name or extension, or null when
/// none of [highlightLanguages] fits.
String? languageForPath(String path) {
  final name = path.split(RegExp(r'[\\/]')).last.toLowerCase();
  final exact = _byName[name];
  if (exact != null) return exact;
  final dot = name.lastIndexOf('.');
  return dot < 0 ? null : _byExtension[name.substring(dot + 1)];
}

/// A cheap guess at [text]'s language: JSON when it parses as an object or
/// array, bash on a shell shebang, python on a python shebang, else null.
String? guessLanguage(String text) {
  final t = text.trimLeft();
  if (t.startsWith('#!')) {
    final first = t.split('\n').first;
    if (first.contains('python')) return 'python';
    if (RegExp(r'\b(ba|z)?sh\b').hasMatch(first)) return 'bash';
    return null;
  }
  if (t.startsWith('{') || t.startsWith('[')) {
    try {
      final v = jsonDecode(t);
      if (v is Map || v is List) return 'json';
    } on FormatException {
      return null;
    }
  }
  return null;
}

/// Colours by role. Null roles keep the base text style.
@immutable
class HighlightPalette {
  /// Creates a palette; the defaults suit a dark background.
  const HighlightPalette({
    this.keyword = const Color(0xFFFF6B5B),
    this.string = const Color(0xFF5FD38D),
    this.number = const Color(0xFFF5C84A),
    this.literal = const Color(0xFFF5C84A),
    this.comment = const Color(0xFF777777),
    this.key = const Color(0xFF4FC3D9),
    this.title = const Color(0xFFBFD9F2),
    this.meta = const Color(0xFFA6A6A6),
    this.punctuation,
    this.addition = const Color(0xFF5FD38D),
    this.deletion = const Color(0xFFFF6B5B),
    this.italicComments = true,
  });

  /// Keywords, tags, doc tags.
  final Color? keyword;

  /// Strings, regexes, escaped characters.
  final Color? string;

  /// Numbers.
  final Color? number;

  /// Literals and symbols where a grammar marks them apart from keywords
  /// (JSON's true, false and null are keywords in highlight.js).
  final Color? literal;

  /// Comments and quotes.
  final Color? comment;

  /// Object keys, attributes, properties, variables.
  final Color? key;

  /// Function, class and section names, types, built-ins.
  final Color? title;

  /// Meta lines (shebangs, decorators, preprocessor) and links.
  final Color? meta;

  /// Punctuation and operators.
  final Color? punctuation;

  /// Added lines in a diff.
  final Color? addition;

  /// Removed lines in a diff.
  final Color? deletion;

  /// Draw comments in italics.
  final bool italicComments;

  /// The highlight.js scope → style map for [base].
  Map<String, TextStyle> theme(TextStyle base) {
    final out = <String, TextStyle>{};
    void put(Color? c, List<String> scopes, {FontStyle? italic, FontWeight? weight}) {
      if (c == null && italic == null && weight == null) return;
      final s = base.copyWith(color: c, fontStyle: italic, fontWeight: weight);
      for (final k in scopes) {
        out[k] = s;
      }
    }

    put(keyword, ['keyword', 'selector-tag', 'doctag', 'formula', 'tag', 'name']);
    put(string, ['string', 'regexp', 'meta-string', 'char.escape', 'template-tag', 'code']);
    put(number, ['number']);
    put(literal, ['literal', 'symbol', 'bullet']);
    put(comment, ['comment', 'quote'], italic: italicComments ? FontStyle.italic : null);
    put(key, ['attr', 'attribute', 'property', 'variable', 'template-variable', 'params', 'selector-attr', 'selector-class', 'selector-id', 'variable.language', 'variable.constant']);
    put(title, ['title', 'title.class', 'title.class.inherited', 'title.function', 'title.function.invoke', 'title.class_', 'title.function_', 'class-title', 'type', 'built_in', 'section', 'selector-pseudo']);
    put(meta, ['meta', 'meta keyword', 'meta prompt', 'link']);
    put(punctuation, ['punctuation', 'operator', 'subst']);
    put(addition, ['addition']);
    put(deletion, ['deletion']);
    out['emphasis'] = base.copyWith(fontStyle: FontStyle.italic);
    out['strong'] = base.copyWith(fontWeight: FontWeight.w700);
    return out;
  }

  @override
  bool operator ==(Object other) =>
      other is HighlightPalette &&
      other.keyword == keyword &&
      other.string == string &&
      other.number == number &&
      other.literal == literal &&
      other.comment == comment &&
      other.key == key &&
      other.title == title &&
      other.meta == meta &&
      other.punctuation == punctuation &&
      other.addition == addition &&
      other.deletion == deletion &&
      other.italicComments == italicComments;

  @override
  int get hashCode => Object.hash(keyword, string, number, literal, comment, key, title, meta,
      punctuation, addition, deletion, italicComments);
}

/// [text] as a coloured span in [language] (one of [highlightLanguages]).
///
/// Plain [style] text when the language is null or unknown, the text is
/// longer than [maxLength] characters, or the grammar fails on it.
TextSpan highlightCode(
  String text, {
  String? language,
  HighlightPalette palette = const HighlightPalette(),
  TextStyle style = const TextStyle(),
  int maxLength = 200000,
}) {
  final plain = TextSpan(text: text, style: style);
  if (language == null || !_grammars.containsKey(language) || text.length > maxLength) {
    return plain;
  }
  try {
    final result = (Highlight()..registerLanguages(_grammars)).highlight(code: text, language: language);
    final renderer = TextSpanRenderer(style, palette.theme(style));
    result.render(renderer);
    return renderer.span ?? plain;
  } catch (_) {
    return plain;
  }
}

/// Read-only coloured code, optionally with line numbers.
///
/// Does not scroll vertically; put it in a scroll view. With [softWrap]
/// off, long lines scroll sideways.
class HighlightedCode extends StatefulWidget {
  /// Creates the view.
  const HighlightedCode({
    super.key,
    required this.text,
    this.language,
    this.palette = const HighlightPalette(),
    this.style = const TextStyle(fontFamily: 'monospace', fontSize: 13, height: 1.45),
    this.lineNumbers = false,
    this.lineNumberColor = const Color(0xFF777777),
    this.gutterPadding = const EdgeInsets.symmetric(horizontal: 12),
    this.softWrap = true,
    this.selectable = true,
    this.maxLength = 200000,
  });

  /// The code.
  final String text;

  /// One of [highlightLanguages]; null shows plain text.
  final String? language;

  /// Colours by role.
  final HighlightPalette palette;

  /// Base text style (family, size, height, default colour).
  final TextStyle style;

  /// Show a line-number gutter (implies no soft wrap, so numbers line up).
  final bool lineNumbers;

  /// Colour of the line numbers.
  final Color lineNumberColor;

  /// Space around the line numbers.
  final EdgeInsets gutterPadding;

  /// Wrap long lines; ignored with [lineNumbers].
  final bool softWrap;

  /// Let the text be selected and copied.
  final bool selectable;

  /// Longest text that is coloured; longer shows plain.
  final int maxLength;

  @override
  State<HighlightedCode> createState() => _HighlightedCodeState();
}

class _HighlightedCodeState extends State<HighlightedCode> {
  TextSpan? _span;

  @override
  void didUpdateWidget(HighlightedCode old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text ||
        old.language != widget.language ||
        old.palette != widget.palette ||
        old.style != widget.style ||
        old.maxLength != widget.maxLength) {
      _span = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final w = widget;
    final span = _span ??= highlightCode(w.text,
        language: w.language, palette: w.palette, style: w.style, maxLength: w.maxLength);
    final wrap = w.softWrap && !w.lineNumbers;
    Widget body = w.selectable
        ? SelectableText.rich(span)
        : RichText(text: span, softWrap: wrap);
    if (!wrap) {
      body = SingleChildScrollView(scrollDirection: Axis.horizontal, child: body);
    }
    if (!w.lineNumbers) return body;
    final lines = '\n'.allMatches(w.text).length + 1;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: w.gutterPadding,
          child: Text(
            [for (var i = 1; i <= lines; i++) '$i'].join('\n'),
            textAlign: TextAlign.right,
            style: w.style.copyWith(color: w.lineNumberColor),
          ),
        ),
        Expanded(child: body),
      ],
    );
  }
}
