/// What a Deputy step did, said plainly. The journal holds tool calls as
/// commands (`bash  cd /work/x && terra brief set --need "…"`); a person
/// reading a conversation wants "Added a need: …". Anything not
/// recognised falls back to "Ran a command", the command itself kept for
/// the record.
library;

/// A step in words: [what] is the line shown; [raw] the command behind it
/// (empty for a model call), shown on request.
class PlainStep {
  const PlainStep(this.what, {this.raw = ''});
  final String what;
  final String raw;
}

PlainStep describeStep(String kind, String text) {
  if (kind == 'model') return const PlainStep('Thought');
  if (kind == 'memory') return const PlainStep('Wrote its working memory');
  if (kind == 'note') return PlainStep(text);
  // A call: "<tool>  <command>".
  final sep = text.indexOf('  ');
  final tool = sep < 0 ? text : text.substring(0, sep);
  var cmd = sep < 0 ? '' : text.substring(sep + 2).trim();
  if (tool == 'read') return PlainStep('Read ${_short(cmd)}', raw: text);
  // The working directory prefix says nothing about what was done.
  cmd = cmd.replaceFirst(RegExp(r'^cd\s+\S+\s*&&\s*'), '');
  final draft = RegExp(r'mizpah\.draft\s+(\w+)\s+(\S+)(.*)').firstMatch(cmd);
  if (draft != null) {
    final verb = draft.group(1)!, slug = draft.group(2)!, rest = draft.group(3)!;
    switch (verb) {
      case 'new':
        final title = _flag(rest, 'title');
        return PlainStep('Started a draft: ${title ?? slug}', raw: cmd);
      case 'show':
        return PlainStep('Pulled up $slug on the desk', raw: cmd);
      case 'discard':
        return PlainStep('Discarded $slug', raw: cmd);
      case 'list':
        return PlainStep('Listed the drafts', raw: cmd);
    }
  }
  final brief = RegExp(r'\bterra\s+brief\s+(\w+)(.*)').firstMatch(cmd);
  if (brief != null) {
    final verb = brief.group(1)!, rest = brief.group(2)!;
    if (verb == 'show') return PlainStep('Read the brief', raw: cmd);
    if (verb == 'set') {
      final parts = <String>[];
      if (rest.contains('--replace-lists')) parts.add('rewrote its lists');
      for (final (flag, noun) in const [
        ('need', 'need'),
        ('deliverable', 'deliverable'),
        ('non-goal', 'non-goal'),
        ('enabler', 'enabler'),
      ]) {
        final values = _flags(rest, flag);
        if (values.isEmpty) continue;
        parts.add(values.length == 1
            ? 'added a $noun: ${_clip(values.first, 90)}'
            : 'added ${values.length} ${noun}s');
      }
      final mission = _flag(rest, 'mission');
      if (mission != null) parts.add('set the mission');
      final title = _flag(rest, 'title');
      if (title != null) parts.add('titled it "${_clip(title, 60)}"');
      final points = _flag(rest, 'budget-points');
      if (points != null) parts.add('set the budget: $points points');
      if (_flag(rest, 'budget-notes') != null) parts.add('wrote the budget notes');
      if (parts.isEmpty) return PlainStep('Changed the brief', raw: cmd);
      final s = parts.join(', ');
      return PlainStep(s[0].toUpperCase() + s.substring(1), raw: cmd);
    }
    return PlainStep('Brief: $verb', raw: cmd);
  }
  if (RegExp(r'\bterra\s+(route|map|known|unknown|probe)\b').hasMatch(cmd)) {
    return PlainStep('Looked at the project\'s ${RegExp(r'\bterra\s+(\w+)').firstMatch(cmd)!.group(1)}', raw: cmd);
  }
  final grep = RegExp(r'\b(?:grep|rg)\b.*?(?:-\w+\s+)*["\x27]?([^"\x27\s]+)').firstMatch(cmd);
  if (cmd.startsWith(RegExp(r'(grep|rg)\b')) && grep != null) {
    return PlainStep('Searched for "${_clip(grep.group(1)!, 50)}"', raw: cmd);
  }
  if (cmd.startsWith(RegExp(r'(cat|head|tail|sed -n|less)\b'))) {
    final file = RegExp(r'(\S+)\s*$').firstMatch(cmd)?.group(1);
    return PlainStep('Read ${file == null ? 'a file' : _short(file)}', raw: cmd);
  }
  if (cmd.startsWith(RegExp(r'(ls|find|tree)\b'))) {
    final dir = RegExp(r'^(?:ls|find|tree)\s+(?:-\S+\s+)*(\S+)').firstMatch(cmd)?.group(1);
    return PlainStep('Looked through ${dir == null || dir.startsWith('-') ? 'the folder' : _short(dir)}', raw: cmd);
  }
  if (cmd.contains(RegExp(r'\b(mkdir|touch|cp|mv)\b'))) return PlainStep('Arranged files', raw: cmd);
  if (cmd.contains(RegExp(r'>\s*\S+|\btee\b|\bcat\s*<<'))) {
    final file = RegExp(r'>\s*(\S+)').firstMatch(cmd)?.group(1);
    return PlainStep('Wrote ${file == null ? 'a file' : _short(file)}', raw: cmd);
  }
  if (cmd.startsWith(RegExp(r'(python|python3)\b'))) return PlainStep('Ran a script', raw: cmd);
  if (cmd.contains('--help') || cmd.startsWith('man ')) return PlainStep('Read the help', raw: cmd);
  return PlainStep('Ran a command', raw: cmd.isEmpty ? text : cmd);
}

/// One `--flag value` (quoted or bare), or null.
String? _flag(String s, String flag) => _flags(s, flag).firstOrNull;

/// Every `--flag value` in order.
List<String> _flags(String s, String flag) {
  final re = RegExp('--$flag(?:=|\\s+)(?:"((?:[^"\\\\]|\\\\.)*)"|\'((?:[^\'\\\\]|\\\\.)*)\'|(\\S+))');
  return [
    for (final m in re.allMatches(s)) (m.group(1) ?? m.group(2) ?? m.group(3) ?? '').replaceAll(r'\"', '"'),
  ];
}

String _short(String path) {
  final parts = path.split('/').where((p) => p.isNotEmpty).toList();
  return parts.length <= 2 ? path : parts.sublist(parts.length - 2).join('/');
}

String _clip(String s, int n) => s.length <= n ? s : '${s.substring(0, n - 1)}…';
