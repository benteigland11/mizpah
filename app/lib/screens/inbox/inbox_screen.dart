import 'package:flutter/material.dart';

import '../../engine/engine.dart';
import '../../models/document.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../state/inbox_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/desk_split.dart';
import '../../widgets/document_sheet.dart';
import '../../widgets/ops.dart';
import '../../widgets/unsaved_dialog.dart';

/// The Inbox: the in-tray. Every document from every project that is waiting
/// on a person — signatures first, then trouble, newest first. Each row
/// names its project; opening one shows the sheet, and signing it walks
/// you into that project's brief.
class InboxScreen extends StatelessWidget {
  const InboxScreen({
    super.key,
    required this.manager,
    required this.briefs,
    required this.nav,
    required this.settings,
  });
  final InboxManager manager;
  final BriefManager briefs;
  final AppNav nav;
  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final items = manager.items;
        final signing = items.where((i) => i.document.awaitingSignature).toList();
        final trouble = items.where((i) => !i.document.awaitingSignature).toList();
        return DeskSplit(
          hasDocument: manager.opened != null,
          selection: manager.openKey,
          listWidth: 420,
          listLabel: 'INBOX',
          count: manager.unread,
          list: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('INBOX', style: opsHeadingStyle(context)),
                        const Spacer(),
                        Text(
                          manager.loading
                              ? 'READING…'
                              : manager.unread > 0
                              ? '${manager.unread} UNREAD'
                              : '${items.length} READ',
                          style: opsLabelStyle(context).copyWith(
                            color: manager.unread > 0 ? cs.primary : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(
                    child: Builder(builder: (context) {
                      final rows = [
                        ..._group(context, 'FOR SIGNATURE', signing, cs.primary),
                        ..._group(context, 'NOTICES', trouble, cs.onSurfaceVariant, dismissible: true),
                      ];
                      return ListView.builder(itemCount: rows.length, itemBuilder: (_, i) => rows[i]);
                    }),
                  ),
                ],
              ),
          // The paper's place, empty: say what the space is, quietly — a
          // label, not a sentence about how good things are.
          sheet: manager.opened == null
                  ? Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.description_outlined, size: 28, color: cs.outlineVariant),
                          const SizedBox(height: Sp.m),
                          Text(
                            manager.loading
                                ? 'READING…'
                                : items.isEmpty
                                    ? 'INBOX CLEAR'
                                    : 'OPEN A NOTICE TO READ IT HERE',
                            style: opsKeyStyle(context).copyWith(color: cs.outline),
                          ),
                        ],
                      ),
                    )
                  : DocumentSheet(
                document: manager.opened?.document,
                project: manager.opened?.project,
                briefs: briefs,
                nav: nav,
                settings: settings,
                dirty: briefs.selectedId == manager.opened?.project.id && briefs.dirty,
                // Desk paper points at a surface or a brief, not a document.
                onOpenLink: (link) async {
                  switch (link) {
                    case 'nav:home':
                      nav.go(AppNav.chat);
                    case 'nav:settings':
                      nav.go(AppNav.style);
                    case final l when l.startsWith('nav:settings/'):
                      nav.goSettings(l.substring('nav:settings/'.length));
                    case 'nav:providers':
                      nav.go(AppNav.providers);
                    case final l when l.startsWith('brief:'):
                      await briefs.select(l.substring('brief:'.length));
                      nav.go(AppNav.brief);
                  }
                },
                onDecide: (accept, reason) async {
                  final item = manager.opened;
                  if (item == null) return;
                  await manager.decide(item, accept: accept, reason: reason);
                  // If that task is the one open behind the Inbox, its brief moved too.
                  if (briefs.selectedId == item.project.id) await briefs.select(item.project.id);
                },
              ),
        );
      },
    );
  }

  List<Widget> _group(
    BuildContext context,
    String label,
    List<AttentionItem> items,
    Color color, {
    bool dismissible = false,
    Widget? headTrailing,
  }) {
    if (items.isEmpty) return const [];
    final cs = Theme.of(context).colorScheme;
    return [
      Container(
        padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, 4),
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
        child: Row(
          children: [
            Text('$label · ${items.length}', style: opsLabelStyle(context).copyWith(color: color)),
            const Spacer(),
            ?headTrailing,
          ],
        ),
      ),
      for (final i in items)
        _NoticeRow(
          key: ValueKey(InboxManager.keyOf(i)),
          item: i,
          selected: InboxManager.keyOf(i) == manager.openKey,
          unread: !manager.isRead(i),
          onTap: () => manager.select(i),
          onRead: () => manager.isRead(i) ? manager.markUnread(i) : manager.markRead(i),
          onDismiss: dismissible ? () => manager.dismiss(i) : null,
          // Desk paper (the Board's, the Deputy's) has no task to walk into.
          onGo: briefs.briefs.any((b) => b.id == i.project.id) ? () => _goTo(context, i.project.id) : null,
        ),
    ];
  }

  /// Walk into the task a sheet came from: its desk, or a draft's brief.
  Future<void> _goTo(BuildContext context, String id) async {
    if (id != briefs.selectedId) {
      if (!await confirmLeave(context, briefs)) return;
      await briefs.select(id);
    }
    final idle = briefs.briefs.where((b) => b.id == id).firstOrNull?.state == 'idle';
    nav.go(idle ? AppNav.brief : AppNav.dailyWork);
  }
}

/// One notice in the tray, laid out to read on a phone as well as a
/// desk: a rail on the left in the state's colour; the kind of paper and
/// its stamp on the first line; the project it came from as the title
/// (weighted while unread); what happened, in words, with the time. The
/// controls sit beside the stamp; the kind label is what gives way.
class _NoticeRow extends StatelessWidget {
  const _NoticeRow({
    super.key,
    required this.item,
    required this.selected,
    required this.unread,
    required this.onTap,
    required this.onRead,
    this.onDismiss,
    this.onGo,
  });
  final AttentionItem item;
  final bool selected;
  final bool unread;
  final VoidCallback onTap;
  final VoidCallback onRead;
  final VoidCallback? onDismiss;

  /// Open the task this sheet came from; null for desk paper.
  final VoidCallback? onGo;

  /// What happened, in one line: the first line of the sheet's first
  /// section (a stop's reason, a change request's finding).
  static String _gist(InboxDocument d) {
    for (final s in d.sections) {
      for (final l in s.lines) {
        final t = l.text.trim();
        if (t.isNotEmpty) return t.split('\n').first;
      }
    }
    return '';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final d = item.document;
    final state = d.past ? cs.outline : DocStamp.colorFor(context, d.status, hot: d.hot, sign: d.awaitingSignature);
    final gist = _gist(d);
    final when = d.at != null ? whenLabel(d.at!) : '';
    return InkWell(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainer : null,
          border: Border(
            left: BorderSide(color: state, width: 3),
            bottom: BorderSide(color: cs.outlineVariant),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(Sp.xl - 3, Sp.s, Sp.s, Sp.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    d.number.isEmpty ? d.kind.label : '${d.kind.label}  ${d.number}',
                    overflow: TextOverflow.ellipsis,
                    softWrap: false,
                    style: opsKeyStyle(context),
                  ),
                ),
                const SizedBox(width: Sp.s),
                // The controls come before the stamp, so the stamp sits in
                // the same slot at the card's edge as it does in Daily work.
                if (onGo != null)
                  SizedBox(
                    width: 24,
                    height: 24,
                    child: OverflowBox(
                      maxWidth: 36,
                      maxHeight: 36,
                      child: IconButton(
                        icon: const Icon(Icons.north_east, size: 14),
                        visualDensity: VisualDensity.compact,
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints.tightFor(width: 36, height: 36),
                        color: cs.outline,
                        tooltip: 'Go to task',
                        onPressed: onGo,
                      ),
                    ),
                  )
                else
                  const SizedBox(width: 24, height: 24),
                Semantics(
                  button: true,
                  toggled: !unread,
                  label: unread ? 'Unread. Mark read' : 'Read. Mark unread',
                  excludeSemantics: true,
                  child: Tooltip(
                    message: unread ? 'Mark read' : 'Mark unread',
                    child: InkWell(
                      onTap: onRead,
                      child: Padding(
                        padding: const EdgeInsets.all(7),
                        child: Container(
                          width: 9,
                          height: 9,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: unread ? ink : Colors.transparent,
                            border: Border.all(color: ink, width: 1.4),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
                // A signature sheet cannot be set aside, but its stamp lines
                // up with the others: the × keeps its place, empty.
                if (onDismiss != null)
                  OpsRemove(onPressed: onDismiss!, tooltip: 'Remove from inbox')
                else
                  const SizedBox(width: 24, height: 24),
                const SizedBox(width: Sp.s),
                DocStamp.slot(DocStamp(d.status, hot: d.hot, sign: d.awaitingSignature, quiet: d.past)),
              ],
            ),
            const SizedBox(height: 2),
            Text(
              item.project.title,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyLarge!.copyWith(
                color: ink,
                fontWeight: unread ? FontWeight.w700 : FontWeight.w400,
                height: 1.3,
              ),
            ),
            if (gist.isNotEmpty || when.isNotEmpty) ...[
              const SizedBox(height: 2),
              Text(
                [if (gist.isNotEmpty) gist, if (when.isNotEmpty) when].join(' · '),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium!.copyWith(color: cs.onSurfaceVariant, height: 1.4),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
