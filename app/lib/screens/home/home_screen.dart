import 'package:flutter/material.dart';

import '../../engine/engine.dart';
import '../../engine/host_watch.dart';
import '../../state/app_nav.dart';
import '../../state/brief_manager.dart';
import '../../state/home_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/document_sheet.dart';
import '../../widgets/host_strip.dart';

/// Home: the in-tray. Every document from every project that is waiting
/// on a person — signatures first, then trouble, newest first. Each row
/// names its project; opening one shows the sheet, and signing it walks
/// you into that project's brief.
class HomeScreen extends StatelessWidget {
  const HomeScreen({
    super.key,
    required this.manager,
    required this.briefs,
    required this.nav,
    required this.host,
  });
  final HomeManager manager;
  final BriefManager briefs;
  final AppNav nav;
  final HostWatch host;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        if (manager.loading || manager.items.isEmpty) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(32, 28, 32, 0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('HOME', style: opsHeadingStyle(context)),
                      const SizedBox(height: Sp.m),
                      Text(
                        manager.loading
                            ? 'Reading the in-tray…'
                            : 'Nothing waits on you. Every change request is signed, '
                                  'nothing is blocked, no run stopped short.',
                        style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
              ),
              HostStrip(watch: host),
            ],
          );
        }
        final items = manager.items;
        final signing = items.where((i) => i.document.awaitingSignature).toList();
        final trouble = items.where((i) => !i.document.awaitingSignature).toList();
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              width: 420,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('HOME', style: opsHeadingStyle(context)),
                        const Spacer(),
                        Text(
                          signing.isNotEmpty
                              ? '${signing.length} FOR SIGNATURE · ${trouble.length} TO LOOK AT'
                              : '${trouble.length} TO LOOK AT',
                          style: opsLabelStyle(context).copyWith(
                            color: signing.isNotEmpty ? cs.primary : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(
                    child: ListView(
                      children: [
                        ..._group(context, 'FOR SIGNATURE', signing, cs.primary),
                        ..._group(context, 'TO LOOK AT', trouble, cs.onSurfaceVariant),
                      ],
                    ),
                  ),
                  HostStrip(watch: host),
                ],
              ),
            ),
            VerticalDivider(width: 1, color: cs.outlineVariant),
            Expanded(
              child: DocumentSheet(
                document: manager.opened?.document,
                project: manager.opened?.project,
                briefs: briefs,
                nav: nav,
              ),
            ),
          ],
        );
      },
    );
  }

  List<Widget> _group(BuildContext context, String label, List<AttentionItem> items, Color color) {
    if (items.isEmpty) return const [];
    final cs = Theme.of(context).colorScheme;
    return [
      Container(
        padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, 4),
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
        child: Text('$label · ${items.length}', style: opsLabelStyle(context).copyWith(color: color)),
      ),
      for (final i in items) ...[
        _ProjectTag(title: i.project.title, state: i.project.state),
        DocumentRow(
          document: i.document,
          selected: identical(i, manager.opened),
          onTap: () => manager.select(i),
        ),
        Divider(height: 1, color: cs.outlineVariant),
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
          Text(
            title.toUpperCase(),
            style: opsKeyStyle(context).copyWith(color: AppTheme.ink(context)),
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
