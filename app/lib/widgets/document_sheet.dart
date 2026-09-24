import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../engine/engine.dart';
import '../models/brief.dart';
import '../models/document.dart';
import '../screens/proposal_view.dart';
import '../state/app_nav.dart';
import '../state/app_settings.dart';
import '../state/brief_manager.dart';
import 'desk_split.dart';
import 'signature.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'ops.dart';

/// How a document is typeset, wherever it is read: the row it occupies in
/// a list and the sheet it opens into. The desk and the work-order folder
/// share these so a work order looks the same in both.

class DocumentRow extends StatelessWidget {
  const DocumentRow({
    super.key,
    required this.document,
    required this.selected,
    required this.onTap,
    this.current = false,
    this.superseded = false,
    this.tint,
    this.trailing,
    this.unread = false,
  });
  final InboxDocument document;
  final bool selected;
  final VoidCallback onTap;

  /// A wash behind the row: the in-tray uses it for paper that needs the
  /// reader's answer.
  final Color? tint;

  /// A control at the row's right edge (dismiss).
  final Widget? trailing;

  /// Not yet opened: the title carries weight, like an unread mail.
  final bool unread;

  /// The standing situation report: the newest briefing. Marked CURRENT.
  final bool current;

  /// An older briefing: history. Drawn quiet, one line, grey stamp.
  final bool superseded;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final d = document;
    // The row's title: the document's, else what it says — a notice has
    // no title, and its first line (the reason, the finding) is the thing.
    final title = d.title.isNotEmpty ? d.title : _gist(d);
    // What is happening now — the standing briefing, a work order being
    // worked — comes forward: drawn a few percent larger than its
    // neighbours, so it overhangs the list's edges, with a thin ink line
    // around it. No shadow (that read as undocked); scale is the depth.
    // Only the standing briefing pops among briefings: an older one keeps
    // its IN WORK stamp on record but is history.
    final live = current ||
        (d.kind != DocKind.briefing && !superseded &&
            const {'IN PROGRESS', 'IN WORK', 'LIVE'}.contains(d.status));
    final row = InkWell(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          // The live card — the standing briefing, a work order being
          // worked — sits on a wash of the accent; the rest on the page.
          // Nothing grows or moves: colour is the emphasis.
          color: selected
              ? cs.surfaceContainer
              : live
              ? cs.primary.withValues(alpha: theme.brightness == Brightness.dark ? 0.14 : 0.07)
              : tint,
          // The rail carries the state, the same as the tray's cards.
          border: Border(
            left: BorderSide(
              color: superseded
                  ? Colors.transparent
                  : d.past
                  ? cs.outline
                  : DocStamp.colorFor(context, d.status, hot: d.hot, sign: d.awaitingSignature),
              width: 3,
            ),
            bottom: BorderSide(color: cs.outlineVariant),
          ),
        ),
        // The stamp slot runs to the card's edge: a small right margin.
        padding: EdgeInsets.fromLTRB(Sp.xl - 3, superseded ? Sp.s : Sp.m, Sp.s, superseded ? Sp.s : Sp.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // The stamp and the controls at the right always fit; the kind
            // and number give way first (a long kind on a narrow list).
            Row(
              children: [
                // The kind and number are one line of text, not two boxes
                // sharing a stretch: a single Text with an ellipsis cannot
                // overflow, however long a work order's task id is.
                Expanded(
                  child: Text.rich(
                    TextSpan(children: [
                      TextSpan(text: d.kind.label, style: opsKeyStyle(context)),
                      if (d.number.isNotEmpty)
                        TextSpan(
                          text: '  ${d.number}',
                          style: AppTheme.mono.copyWith(
                            fontSize: 12,
                            color: superseded ? cs.onSurfaceVariant : cs.onSurface,
                          ),
                        ),
                    ]),
                    maxLines: 1,
                    softWrap: false,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (current) ...[
                  Text(
                    'CURRENT',
                    style: opsKeyStyle(context).copyWith(
                      color: AppTheme.ink(context),
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(width: Sp.s),
                ],
                const SizedBox(width: Sp.s),
                if (superseded)
                  // History: the moment it was written, not a state it is in.
                  Text(d.at != null ? whenLabel(d.at!) : 'earlier', softWrap: false, overflow: TextOverflow.ellipsis, style: opsKeyStyle(context))
                else
                  DocStamp.slot(DocStamp(d.status, hot: d.hot, sign: d.awaitingSignature, quiet: d.past)),
                if (trailing != null) ...[const SizedBox(width: Sp.s), trailing!],
              ],
            ),
            if (title.isNotEmpty) const SizedBox(height: 6),
            if (title.isNotEmpty)
            Text(
              title,
              maxLines: superseded ? 1 : 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium!.copyWith(
                color: superseded
                    ? cs.onSurfaceVariant
                    : selected || unread
                    ? AppTheme.ink(context)
                    : cs.onSurface,
                fontWeight: unread ? FontWeight.w700 : null,
                height: 1.35,
              ),
            ),
            if (!superseded) ...[
              const SizedBox(height: 4),
              Text(
                '${d.from} · ${d.at != null ? whenLabel(d.at!) : 'time not recorded'}',
                style: opsKeyStyle(context),
              ),
            ],
          ],
        ),
      ),
    );
    return row;
  }
}

/// What a document says, in one line: the first line of its first section.
String _gist(InboxDocument d) {
  for (final s in d.sections) {
    for (final l in s.lines) {
      final t = l.text.trim();
      if (t.isNotEmpty) return t.split('\n').first;
    }
  }
  return '';
}

/// The stamp in the corner: mono caps, red when the document is a problem
/// or is waiting on the reader.
class DocStamp extends StatelessWidget {
  const DocStamp(this.text, {super.key, required this.hot, required this.sign, this.quiet = false, this.width});
  final String text;
  final bool hot;
  final bool sign;

  /// A fixed box with the word centred; unused by the lists, which anchor
  /// a natural-width stamp in a slot instead (see [slot]).
  final double? width;

  /// The slot a list gives its stamps: the common words fit (GATE GREEN,
  /// IN WORK, STOPPED, COMPLETE); a rare long one ellipsises rather than
  /// widen the column.
  static const columnWidth = 100.0;

  /// The stamp at its own width, anchored at the left of a fixed slot at
  /// the card's right edge, so every stamp in a list starts at the same x
  /// whatever its word. Right-aligned they gave a ragged left edge; boxed
  /// to one width they grew.
  static Widget slot(Widget stamp) => SizedBox(
        width: columnWidth,
        child: Align(alignment: Alignment.centerLeft, child: stamp),
      );

  /// History: grey whatever the state was.
  final bool quiet;

  /// One colour per state, used by the stamp and by anything that groups
  /// documents by it. Red is trouble or a signature owed; green is done;
  /// ink is moving; amber is waiting; grey is inert.
  static Color colorFor(BuildContext context, String status, {bool hot = false, bool sign = false}) {
    final cs = Theme.of(context).colorScheme;
    if (sign) return cs.primary;
    return switch (status) {
      'IN PROGRESS' || 'IN WORK' || 'LIVE' => AppTheme.ink(context),
      'GATE GREEN' || 'CLOSED' || 'GO' || 'COMPLETE' || 'ACCEPTED' => AppTheme.added(context),
      'QUEUED' || 'OPENING' || 'NO ACTION' || 'SENT' => cs.error, // amber in this theme: waiting, watch
      'BLOCKED' || 'ABORTED' || 'GATE RED' || 'STALLED' || 'OUTAGE' || 'STOPPED' || 'OPEN' || 'NEEDS GUIDANCE' || 'FOR SIGNATURE' => cs.primary,
      'CANCELLED' || 'REJECTED' || 'READ' => cs.outline,
      _ => hot ? cs.primary : cs.onSurfaceVariant,
    };
  }

  @override
  Widget build(BuildContext context) {
    final color = quiet
        ? Theme.of(context).colorScheme.outline
        : colorFor(context, text, hot: hot, sign: sign);
    // Read aloud as a state, with what the colour says: the ring alone
    // gives a reader the word without the meaning.
    return Semantics(
      label: 'Status: ${text.toLowerCase()}${quiet ? ', past' : sign ? ', for signature' : hot ? ', needs attention' : ''}',
      excludeSemantics: true,
      child: Container(
      width: width,
      alignment: width == null ? null : Alignment.center,
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        border: Border.all(color: color, width: 1.2),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Text(
        text,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: AppTheme.mono.copyWith(
          fontSize: 11,
          letterSpacing: 1.2,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
      ),
    );
  }
}

class DocumentSheet extends StatelessWidget {
  /// The paper's width: what the desk lays everything else around.
  static const sheetWidth = 820.0;

  const DocumentSheet({
    super.key,
    required this.document,
    required this.briefs,
    required this.nav,
    this.project,
    this.onDecide,
    this.onOpenLink,
    this.dirty = false,
    this.settings,
  });

  /// Say something to the run: a reply at the foot of a briefing or a
  /// notice. The controller reads it at its next briefing; on a stopped
  /// run the loop is relaunched to answer. Null where there is no run.

  final InboxDocument? document;
  final BriefManager briefs;
  final AppNav nav;

  /// The document's project when it is not the selected one (the home
  /// page reads across projects); the sheet switches to it before signing.
  final BriefSummary? project;

  /// Sign here. When given, an open change request carries its own
  /// decision block — reason, ACCEPT, REJECT — and a quiet link to the
  /// brief for inspection. Without it the sheet only offers the link.
  final Future<void> Function(bool accept, String reason)? onDecide;

  /// Follow a line's link (`<kind>:<number>`) to that document.
  final void Function(String link)? onOpenLink;

  /// Unsaved brief edits block signing (the decision rewrites the brief).
  final bool dirty;

  /// Who is at the desk: a decision is signed in their name and their mark
  /// is filed with it. Without a name set, nothing can be signed.
  final AppSettings? settings;

  /// Read the change request over the brief it amends — inspection, not
  /// the only way to sign.
  Future<void> _openInBrief() async {
    final d = document;
    if (d == null) return;
    final p = project;
    if (p != null && p.id != briefs.selectedId) await briefs.select(p.id);
    briefs.openProposal(d.proposalId);
    nav.go(AppNav.brief);
  }

  @override
  Widget build(BuildContext context) {
    final d = document;
    if (d == null) return const SizedBox.shrink();
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final body = theme.textTheme.bodyLarge!.copyWith(
      fontSize: 16,
      color: ink,
      height: 1.5,
    );
    final projectTitle = project?.title ?? briefs.draft?.title ?? '';

    // The paper keeps its width; the margin around it gives way first,
    // then the paper's own inner margin, before the text has to.
    return LayoutBuilder(builder: (context, c) {
      final outer = DeskSplit.paddingFor(c.maxWidth, sheetWidth);
      final inner = c.maxWidth - outer.horizontal >= sheetWidth ? 56.0 : 32.0;
      return SingleChildScrollView(
      padding: outer,
      child: Center(
        child: OpsSheet(
          maxWidth: sheetWidth,
          padding: EdgeInsets.fromLTRB(inner, 48, inner, 44),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    // A short identifier rides the title line (#003, CR-001);
                    // a work order's task id is a subtitle beneath it.
                    child: d.kind == DocKind.workOrder
                        ? Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                d.kind.label,
                                style: opsHeadingStyle(context).copyWith(fontSize: 22, color: ink),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                d.number,
                                style: AppTheme.mono.copyWith(fontSize: 15, color: cs.onSurfaceVariant),
                              ),
                            ],
                          )
                        : Text(
                            d.number.isEmpty ? d.kind.label : '${d.kind.label}  ${d.number}',
                            style: opsHeadingStyle(context).copyWith(fontSize: 22, color: ink),
                          ),
                  ),
                  DocStamp(d.status, hot: d.hot, sign: d.awaitingSignature, quiet: d.past),
                ],
              ),
              const SizedBox(height: 24),
              // Desk paper (the Board's, the Deputy's) is not a task's: no task line.
              if (d.kind != DocKind.boardNotice && d.kind != DocKind.deputyNotice) _HeaderRow('Task', projectTitle),
              _HeaderRow('Date', d.at != null ? whenLabel(d.at!) : '—'),
              _HeaderRow('From', d.from),
              for (final (k, v) in d.header) _HeaderRow(k, v),
              const SizedBox(height: 12),
              Divider(color: cs.outline, thickness: 1.5),
              if (d.kind == DocKind.workOrder) ...[
                const SizedBox(height: 20),
                Text(
                  d.title,
                  style: body.copyWith(
                    fontWeight: FontWeight.w700,
                    fontSize: 17,
                  ),
                ),
              ],
              for (final s in d.sections) ...[
                const SizedBox(height: 28),
                _SectionHeading(s),
                const SizedBox(height: 12),
                if (s.heading == 'Proposed amendment' && d.patch != null)
                  _Amendment(
                    patch: d.patch!,
                    briefs: briefs,
                    projectId: project?.id ?? briefs.selectedId,
                    body: body,
                  )
                else ...[
                  for (final l in s.lines) _Line(l, body: body, onOpenLink: onOpenLink),
                  if (s.isTable) _SheetTable(section: s),
                  // A decided change request carries the signature under its
                  // decision: the one stored on it, never today's settings.
                  if (s.heading == 'Decision' && d.signedBy.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    SignatureBlock.stored(
                      signedBy: d.signedBy,
                      image: d.signature,
                      signedAt: d.decidedAt,
                      label: d.status == 'ACCEPTED' ? 'ACCEPTED AND SIGNED' : 'REJECTED AND SIGNED',
                    ),
                  ],
                  if (s.footnote.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text(s.footnote, style: opsKeyStyle(context)),
                  ],
                ],
              ],
              if (d.awaitingSignature && d.proposalId != null) ...[
                const SizedBox(height: 32),
                Divider(color: cs.outline, thickness: 1.5),
                const SizedBox(height: 20),
                if (onDecide != null)
                  _Signature(
                    key: ValueKey('${project?.id}/${d.proposalId}'),
                    dirty: dirty,
                    signer: settings,
                    onDecide: onDecide!,
                    onInspect: _openInBrief,
                  )
                else
                  Row(
                    children: [
                      Text(
                        'FOR SIGNATURE',
                        style: opsLabelStyle(context).copyWith(color: cs.primary),
                      ),
                      const Spacer(),
                      FilledButton(
                        onPressed: _openInBrief,
                        child: const Text('OPEN AGAINST THE BRIEF'),
                      ),
                    ],
                  ),
              ],
            ],
          ),
        ),
      ),
    );
    });
  }
}

class _SectionHeading extends StatelessWidget {
  const _SectionHeading(this.section);
  final DocSection section;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      children: [
        Text(
          section.heading.toUpperCase(),
          style: opsLabelStyle(context).copyWith(fontSize: 12),
        ),
        if (section.count != null) ...[
          const SizedBox(width: Sp.s),
          Text(
            '(${section.count})',
            style: opsLabelStyle(context).copyWith(color: cs.primary),
          ),
        ],
        const SizedBox(width: Sp.m),
        Expanded(child: Divider(color: cs.outlineVariant)),
      ],
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.line, {required this.body, this.onOpenLink});
  final DocLine line;
  final TextStyle body;
  final void Function(String link)? onOpenLink;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = line.mono
        ? AppTheme.mono.copyWith(fontSize: 14, color: body.color, height: 1.5)
        : body;
    var styled = line.emphasis
        ? text.copyWith(color: cs.error, fontWeight: FontWeight.w600)
        : text;
    // A line that points at another document reads as a link and opens it.
    final linked = line.link.isNotEmpty && onOpenLink != null;
    if (linked) {
      styled = styled.copyWith(color: cs.primary, decoration: TextDecoration.underline, decorationStyle: TextDecorationStyle.dotted);
    }
    Widget wrap(Widget child) => linked
        ? MouseRegion(cursor: SystemMouseCursors.click, child: GestureDetector(onTap: () => onOpenLink!(line.link), child: child))
        : child;
    if (line.quote) {
      return Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.only(left: 14),
        decoration: BoxDecoration(
          border: Border(left: BorderSide(color: cs.outline, width: 2)),
        ),
        child: Text(
          line.text,
          style: body.copyWith(color: cs.onSurfaceVariant, fontSize: 15),
        ),
      );
    }
    if (line.rich && onOpenLink != null) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: _RichLine(line: line, style: styled, linkColor: cs.primary, onOpenLink: onOpenLink!),
      );
    }
    if (line.lead.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: wrap(Text(line.text, style: styled)),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: wrap(Row(
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: [
          SizedBox(
            width: 220,
            child: Text(
              line.lead,
              style: AppTheme.mono.copyWith(
                fontSize: 13,
                color: linked ? cs.primary : line.emphasis ? cs.error : cs.onSurfaceVariant,
                fontWeight: line.emphasis || linked ? FontWeight.w600 : null,
              ),
            ),
          ),
          const SizedBox(width: Sp.m),
          Expanded(child: Text(line.text, style: styled)),
        ],
      )),
    );
  }
}

/// A line with links in it: the plain runs as text, each linked run in
/// the link colour with a dotted underline, opening its target on tap.
/// Stateful for the recognisers, which must be disposed.
class _RichLine extends StatefulWidget {
  const _RichLine({required this.line, required this.style, required this.linkColor, required this.onOpenLink});
  final DocLine line;
  final TextStyle style;
  final Color linkColor;
  final void Function(String link) onOpenLink;

  @override
  State<_RichLine> createState() => _RichLineState();
}

class _RichLineState extends State<_RichLine> {
  final _taps = <TapGestureRecognizer>[];

  @override
  void dispose() {
    for (final t in _taps) {
      t.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    for (final t in _taps) {
      t.dispose();
    }
    _taps.clear();
    final link = widget.style.copyWith(color: widget.linkColor, decoration: TextDecoration.underline, decorationStyle: TextDecorationStyle.dotted);
    return Text.rich(TextSpan(
      style: widget.style,
      children: [
        for (final (text, target) in widget.line.runs)
          if (target.isEmpty)
            TextSpan(text: text)
          else
            TextSpan(
              text: text,
              style: link,
              mouseCursor: SystemMouseCursors.click,
              recognizer: () {
                final t = TapGestureRecognizer()..onTap = () => widget.onOpenLink(target);
                _taps.add(t);
                return t;
              }(),
            ),
      ],
    ));
  }
}

class _HeaderRow extends StatelessWidget {
  const _HeaderRow(this.k, this.v);
  final String k;
  final String v;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: [
          SizedBox(
            width: 150,
            child: Text(
              '${k.toUpperCase()}:',
              style: opsLabelStyle(context).copyWith(fontSize: 12),
            ),
          ),
          Expanded(
            child: Text(
              v,
              style: theme.textTheme.bodyLarge!.copyWith(
                color: AppTheme.ink(context),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

String whenLabel(DateTime d) {
  final l = d.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  return '${l.day} ${months[l.month - 1]} ${l.year}, ${two(l.hour)}:${two(l.minute)}';
}

/// The decision block at the foot of an open change request: a reason,
/// ACCEPT in green, REJECT outlined in red, and a quiet way to read it
/// against the brief first. Mirrors the memo page so signing feels the
/// same wherever the paper is.
class _Signature extends StatefulWidget {
  const _Signature({
    super.key,
    required this.dirty,
    required this.onDecide,
    required this.onInspect,
    this.signer,
  });
  final bool dirty;
  final AppSettings? signer;
  final Future<void> Function(bool accept, String reason) onDecide;
  final VoidCallback onInspect;

  @override
  State<_Signature> createState() => _SignatureState();
}

class _SignatureState extends State<_Signature> {
  final _reason = TextEditingController();
  bool busy = false;
  String? error;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  Future<void> _go(bool accept) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.onDecide(accept, _reason.text);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final unsigned = widget.signer != null && !widget.signer!.canSign;
    final enabled = !busy && !widget.dirty && !unsigned;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Text('FOR SIGNATURE', style: opsLabelStyle(context).copyWith(color: cs.primary)),
            const Spacer(),
            TextButton(
              onPressed: widget.onInspect,
              child: Text('READ AGAINST THE BRIEF', style: opsLabelStyle(context)),
            ),
          ],
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _reason,
          enabled: enabled,
          minLines: 2,
          maxLines: 5,
          style: theme.textTheme.bodyLarge!.copyWith(color: ink),
          decoration: InputDecoration(
            labelText: 'REASON (OPTIONAL)',
            labelStyle: opsLabelStyle(context).copyWith(fontSize: 12),
            hintText: 'What you saw. Kept on the change request; a rejection\'s '
                'reason is why it will not be proposed again.',
            border: OutlineInputBorder(borderSide: BorderSide(color: cs.outline)),
            alignLabelWithHint: true,
          ),
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            SizedBox(
              width: 88,
              child: Text('DECISION:', style: opsLabelStyle(context).copyWith(fontSize: 13)),
            ),
            FilledButton(
              onPressed: enabled ? () => _go(true) : null,
              style: FilledButton.styleFrom(
                backgroundColor: AppTheme.added(context),
                foregroundColor: cs.surface,
                padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 18),
              ),
              child: const Text('ACCEPT'),
            ),
            const SizedBox(width: 12),
            OutlinedButton(
              onPressed: enabled ? () => _go(false) : null,
              style: OutlinedButton.styleFrom(
                foregroundColor: AppTheme.removed(context),
                side: BorderSide(color: AppTheme.removed(context)),
                padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 18),
              ),
              child: const Text('REJECT'),
            ),
            const SizedBox(width: 20),
            if (busy)
              Text('SIGNING…', style: opsLabelStyle(context))
            else if (widget.dirty)
              Flexible(child: Text('SAVE YOUR BRIEF EDITS FIRST', style: opsLabelStyle(context).copyWith(color: cs.error)))
            else if (unsigned)
              Flexible(child: Text('SET YOUR NAME UNDER SETTINGS › SIGNATURE FIRST', style: opsLabelStyle(context).copyWith(color: cs.error))),
          ],
        ),
        // A failed signing is said in full on its own line, however narrow the sheet.
        if (error != null && !busy) ...[
          const SizedBox(height: 10),
          OpsError(error!),
        ],
        if (widget.signer?.canSign == true) ...[
          const SizedBox(height: 10),
          // Whose name goes on it. The mark itself is filed on signing,
          // never drawn on paper that is still unsigned.
          Text('Signs as ${widget.signer!.signerLine}', style: opsKeyStyle(context)),
        ],
      ],
    );
  }
}


/// The amendment as the memo page draws it: each patch entry stated in a
/// sentence and shown as a diff of the brief section it touches. Needs the
/// brief for the context lines; the open task's draft when it is this
/// task, otherwise a read off disk.
class _Amendment extends StatelessWidget {
  const _Amendment({
    required this.patch,
    required this.briefs,
    required this.projectId,
    required this.body,
  });
  final Map<String, dynamic> patch;
  final BriefManager briefs;
  final String? projectId;
  final TextStyle body;

  @override
  Widget build(BuildContext context) {
    final id = projectId;
    if (id != null && id == briefs.selectedId && briefs.draft != null) {
      return _changes(briefs.draft!);
    }
    if (id == null) return const SizedBox.shrink();
    return FutureBuilder<Brief>(
      key: ValueKey(id),
      future: briefs.peek(id),
      builder: (context, snap) {
        final b = snap.data;
        if (b == null) return const SizedBox(height: 24);
        return _changes(b);
      },
    );
  }

  Widget _changes(Brief b) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      for (final e in patch.entries) ...[
        ProposalChange(
          brief: b,
          key_: e.key,
          value: e.value,
          body: body,
          proposalPatch: patch,
        ),
        const SizedBox(height: 24),
      ],
    ],
  );
}

/// A section's table: mono column heads, ruled rows, the first column in
/// ink. Set as a real grid so cells line up across rows.
class _SheetTable extends StatelessWidget {
  const _SheetTable({required this.section});
  final DocSection section;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final head = opsKeyStyle(context);
    final cell = AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurface);
    // Every column sizes to its content except one, which takes the rest;
    // a long-text column left intrinsic squeezed the first to a letter a
    // line (the change-request table).
    final flex = section.flexColumn ?? section.columns.length - 1;
    return Table(
      columnWidths: {
        for (var i = 0; i < section.columns.length; i++)
          i: i == flex ? const FlexColumnWidth() : const IntrinsicColumnWidth(),
      },
      defaultVerticalAlignment: TableCellVerticalAlignment.baseline,
      textBaseline: TextBaseline.alphabetic,
      border: TableBorder(
        horizontalInside: BorderSide(color: cs.outlineVariant),
        bottom: BorderSide(color: cs.outline),
      ),
      children: [
        TableRow(
          decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outline, width: 1.5))),
          children: [
            for (final c in section.columns)
              Padding(padding: const EdgeInsets.fromLTRB(0, 4, 16, 6), child: Text(c.toUpperCase(), style: head)),
          ],
        ),
        for (final r in section.rows)
          TableRow(
            children: [
              for (var i = 0; i < section.columns.length; i++)
                Padding(
                  padding: const EdgeInsets.fromLTRB(0, 8, 16, 8),
                  child: Text(
                    i < r.length ? r[i] : '',
                    style: i == 0 ? cell.copyWith(color: ink, fontWeight: FontWeight.w700) : cell,
                  ),
                ),
            ],
          ),
      ],
    );
  }
}
