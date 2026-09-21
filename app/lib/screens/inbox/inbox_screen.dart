import 'package:flutter/material.dart';

import '../../engine/engine.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../state/inbox_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/desk_split.dart';
import '../../widgets/document_sheet.dart';
import '../../widgets/ops.dart';

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
          selection: manager.opened,
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
                        ..._group(context, 'TO LOOK AT', trouble, cs.onSurfaceVariant, dismissible: true),
                      ];
                      return ListView.builder(itemCount: rows.length, itemBuilder: (_, i) => rows[i]);
                    }),
                  ),
                ],
              ),
          sheet: manager.opened == null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 48),
                        child: Text(
                          manager.loading
                              ? 'Reading the in-tray…'
                              : 'Nothing waits on you. Every change request is '
                                    'signed, nothing is blocked, no run stopped short.',
                          textAlign: TextAlign.center,
                          style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                        ),
                      ),
                    )
                  : DocumentSheet(
                document: manager.opened?.document,
                project: manager.opened?.project,
                briefs: briefs,
                nav: nav,
                settings: settings,
                dirty: briefs.selectedId == manager.opened?.project.id && briefs.dirty,
                onDecide: (accept, reason) async {
                  final item = manager.opened;
                  if (item == null) return;
                  await manager.decide(item, accept: accept, reason: reason);
                  // If that task is the one open behind the Inbox, its brief moved too.
                  if (briefs.selectedId == item.project.id) await briefs.select(item.project.id);
                },
                onReply: (text) async {
                  final item = manager.opened;
                  if (item != null) await manager.reply(item, text);
                },
                running: manager.opened?.project.running ?? false,
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
    // Paper that needs an answer sits on a faint wash of the accent.
    final tint = dismissible ? null : cs.primary.withValues(alpha: 0.07);
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
      // Order is by time; a run of consecutive items from one task shares
      // a single task line rather than repeating it above each sheet.
      for (final (n, i) in items.indexed) ...[
        if (n == 0 || items[n - 1].project.id != i.project.id)
          _ProjectTag(title: i.project.title, state: i.project.state),
        DocumentRow(
          document: i.document,
          selected: identical(i, manager.opened),
          onTap: () => manager.select(i),
          tint: tint,
          unread: !manager.isRead(i),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Read / unread toggle: a dot while unread, hollow once read.
              Tooltip(
                message: manager.isRead(i) ? 'Mark unread' : 'Mark read',
                child: InkWell(
                  onTap: () => manager.isRead(i) ? manager.markUnread(i) : manager.markRead(i),
                  child: Padding(
                    padding: const EdgeInsets.all(6),
                    child: Container(
                      width: 9,
                      height: 9,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: manager.isRead(i) ? Colors.transparent : AppTheme.ink(context),
                        border: Border.all(color: AppTheme.ink(context), width: 1.4),
                      ),
                    ),
                  ),
                ),
              ),
              if (dismissible) OpsRemove(onPressed: () => manager.dismiss(i), tooltip: 'Remove from inbox'),
            ],
          ),
        ),
        if (n == items.length - 1 || items[n + 1].project.id != i.project.id)
          Divider(height: 1, color: cs.outlineVariant)
        else
          Divider(height: 1, indent: Sp.xl, color: cs.outlineVariant),
      ],
    ];
  }
}

/// The project line above a row: which office this paper came from.
class _ProjectTag extends StatelessWidget {
  const _ProjectTag({required this.title, required this.state});
  final String title;
  final String state;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.s, Sp.l, 0),
      child: Row(
        children: [
          Flexible(
            child: Text(
              title.toUpperCase(),
              overflow: TextOverflow.ellipsis,
              softWrap: false,
              style: opsKeyStyle(context).copyWith(color: AppTheme.ink(context)),
            ),
          ),
          if (state == 'live') ...[
            const SizedBox(width: Sp.s),
            Text('LIVE', style: opsKeyStyle(context).copyWith(color: DocStamp.colorFor(context, 'LIVE'))),
          ] else if (state == 'stopped') ...[
            const SizedBox(width: Sp.s),
            Text('STOPPED', style: opsKeyStyle(context).copyWith(color: cs.primary)),
          ],
        ],
      ),
    );
  }
}
