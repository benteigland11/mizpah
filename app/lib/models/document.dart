/// The paperwork of a run. Everything that arrives on the desk is one of
/// these: a briefing, a work order, a change request, an anomaly report,
/// the notice of task stopped. The document is data; screens typeset it.
library;

enum DocKind {
  briefing,
  workOrder,
  changeRequest,
  anomaly,
  closing,
  completion,
  memo,
  boardNotice,
  deputyNotice;

  String get label => switch (this) {
    briefing => 'DAILY BRIEFING',
    workOrder => 'WORK ORDER',
    changeRequest => 'CHANGE REQUEST',
    anomaly => 'ANOMALY REPORT',
    closing => 'NOTICE OF TASK STOPPED',
    completion => 'NOTICE OF TASK COMPLETION',
    memo => 'MEMO TO THE LOOP',
    boardNotice => 'NOTICE FROM THE BOARD',
    deputyNotice => 'NOTICE FROM THE DEPUTY',
  };
}

/// One line of a section. [mono] for ids and values; [emphasis] for a
/// stamp or a warning; [sub] for the grey detail that follows a lead.
class DocLine {
  const DocLine(
    this.text, {
    this.lead = '',
    this.mono = false,
    this.emphasis = false,
    this.quote = false,
    this.link = '',
    this.rich = false,
  });
  final String text;

  /// A short mono prefix: an unknown id, a turn number, a patch key.
  final String lead;
  final bool mono;
  final bool emphasis;
  final bool quote;

  /// Another document this line points at, as `<kind>:<number>`
  /// (`changeRequest:CR-001`, `workOrder:measure_parts_csv`). The sheet
  /// sets the line as a link and the screen opens that document.
  final String link;

  /// The text carries inline links as `[label](target)`, to be set as
  /// links in place; a plain line never is, so quoted markdown stays text.
  final bool rich;

  /// A rich line's text cut into runs: `(text, target)`, target empty for
  /// plain text.
  static final _inline = RegExp(r'\[([^\]]+)\]\(([^)]+)\)');
  List<(String, String)> get runs {
    if (!rich) return [(text, '')];
    final out = <(String, String)>[];
    var at = 0;
    for (final m in _inline.allMatches(text)) {
      if (m.start > at) out.add((text.substring(at, m.start), ''));
      out.add((m.group(1)!, m.group(2)!));
      at = m.end;
    }
    if (at < text.length) out.add((text.substring(at), ''));
    return out;
  }

  Map<String, dynamic> toJson() => {
    'text': text,
    if (lead.isNotEmpty) 'lead': lead,
    if (mono) 'mono': true,
    if (emphasis) 'emphasis': true,
    if (quote) 'quote': true,
    if (link.isNotEmpty) 'link': link,
    if (rich) 'rich': true,
  };

  factory DocLine.fromJson(Map<String, dynamic> j) => DocLine(
    j['text'] as String? ?? '',
    lead: j['lead'] as String? ?? '',
    mono: j['mono'] == true,
    emphasis: j['emphasis'] == true,
    quote: j['quote'] == true,
    link: j['link'] as String? ?? '',
    rich: j['rich'] == true,
  );
}

class DocSection {
  const DocSection(this.heading, this.lines, {this.count, this.columns = const [], this.rows = const [], this.footnote = '', this.flexColumn});
  final String heading;
  final List<DocLine> lines;

  /// Shown after the heading when the section is long enough to fold.
  final int? count;

  /// A table: column headings and rows of cells, set by the sheet as a
  /// real grid. Lines (if any) render above it.
  final List<String> columns;
  final List<List<String>> rows;

  /// Small print under the section: what a marker means.
  final String footnote;

  /// The one column that takes the remaining width (long text); the
  /// others size to their content. Default: the last column.
  final int? flexColumn;

  bool get isTable => columns.isNotEmpty;

  Map<String, dynamic> toJson() => {
    'heading': heading,
    'lines': [for (final l in lines) l.toJson()],
    if (count != null) 'count': count,
    if (columns.isNotEmpty) 'columns': columns,
    if (rows.isNotEmpty) 'rows': rows,
    if (footnote.isNotEmpty) 'footnote': footnote,
    if (flexColumn != null) 'flex_column': flexColumn,
  };

  factory DocSection.fromJson(Map<String, dynamic> j) => DocSection(
    j['heading'] as String? ?? '',
    [
      for (final l in (j['lines'] as List? ?? const []))
        DocLine.fromJson((l as Map).cast<String, dynamic>()),
    ],
    count: j['count'] as int?,
    columns: (j['columns'] as List? ?? const []).cast<String>(),
    rows: [for (final r in (j['rows'] as List? ?? const [])) (r as List).cast<String>()],
    footnote: j['footnote'] as String? ?? '',
    flexColumn: j['flex_column'] as int?,
  );
}

class InboxDocument {
  const InboxDocument({
    required this.kind,
    required this.number,
    required this.title,
    required this.at,
    required this.from,
    required this.status,
    required this.header,
    required this.sections,
    this.hot = false,
    this.past = false,
    this.awaitingSignature = false,
    this.proposalId,
    this.patch,
    this.decidedAt,
    this.signedBy = '',
    this.signature,
  });

  final DocKind kind;

  /// `#003`, `` `build_cli` ``, `CR-001`.
  final String number;

  /// The one line the inbox row shows.
  final String title;
  final DateTime? at;
  final String from;

  /// The stamp: GO, NO-GO, CLOSED, ABORTED, OPEN…
  final String status;

  /// Header block after Project · Date · From.
  final List<(String, String)> header;
  final List<DocSection> sections;

  /// Red stamp: NO-GO, ABORTED, an anomaly.
  final bool hot;

  /// Grey stamp, whatever the word: it happened, and it is over (an
  /// outage the model has answered since).
  final bool past;
  final bool awaitingSignature;

  /// Who signed, as stored on the document ("Name, Title"), and the file
  /// of the mark filed with it. Empty when the document is unsigned or was
  /// decided before signatures were kept.
  final String signedBy;
  final String? signature;

  /// For a change request: the proposal it is, so the memo can open.
  final String? proposalId;

  /// For a change request: the raw amendment, so the sheet can draw it as
  /// a diff against the brief rather than as lines.
  final Map<String, dynamic>? patch;

  /// For a decided change request: when, if the brief recorded it.
  final DateTime? decidedAt;

  /// The same document stamped with when it was sent.
  InboxDocument dated(DateTime at) => InboxDocument.fromJson({...toJson(), 'at': at.toUtc().toIso8601String()});

  Map<String, dynamic> toJson() => {
    'kind': kind.name,
    'number': number,
    'title': title,
    if (at != null) 'at': at!.toUtc().toIso8601String(),
    'from': from,
    'status': status,
    'header': [
      for (final (k, v) in header) {'k': k, 'v': v},
    ],
    'sections': [for (final s in sections) s.toJson()],
    if (hot) 'hot': true,
    if (past) 'past': true,
    if (awaitingSignature) 'awaiting_signature': true,
    if (proposalId != null) 'proposal_id': proposalId,
    if (patch != null) 'patch': patch,
    if (decidedAt != null) 'decided_at': decidedAt!.toUtc().toIso8601String(),
    if (signedBy.isNotEmpty) 'signed_by': signedBy,
    if (signature != null) 'signature': signature,
  };

  factory InboxDocument.fromJson(Map<String, dynamic> j) => InboxDocument(
    kind: DocKind.values.firstWhere(
      (k) => k.name == j['kind'],
      orElse: () => DocKind.briefing,
    ),
    number: j['number'] as String? ?? '',
    title: j['title'] as String? ?? '',
    at: DateTime.tryParse(j['at'] as String? ?? ''),
    from: j['from'] as String? ?? '',
    status: j['status'] as String? ?? '',
    header: [
      for (final h in (j['header'] as List? ?? const []))
        ((h as Map)['k'] as String, h['v'] as String),
    ],
    sections: [
      for (final s in (j['sections'] as List? ?? const []))
        DocSection.fromJson((s as Map).cast<String, dynamic>()),
    ],
    hot: j['hot'] == true,
    past: j['past'] == true,
    awaitingSignature: j['awaiting_signature'] == true,
    proposalId: j['proposal_id'] as String?,
    patch: (j['patch'] as Map?)?.cast<String, dynamic>(),
    decidedAt: DateTime.tryParse(j['decided_at'] as String? ?? ''),
    signedBy: j['signed_by'] as String? ?? '',
    signature: j['signature'] as String?,
  );
}
