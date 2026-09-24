import 'package:flutter/material.dart';

import '../../models/document.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../state/daily_work_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/desk_split.dart';
import '../../widgets/working_over.dart';
import '../../widgets/document_sheet.dart';
import '../../widgets/ops.dart';

/// The desk. What arrived, newest on top, down the left; the opened
/// document as a sheet on the right. Nothing here is a dashboard: a
/// briefing is read, a work order is read, a change request is signed.
class DailyWorkScreen extends StatelessWidget {
  const DailyWorkScreen({
    super.key,
    required this.manager,
    required this.briefs,
    required this.nav,
    required this.settings,
  });
  final DailyWorkManager manager;
  final BriefManager briefs;
  final AppNav nav;
  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        return WorkingOver(
          working: manager.working,
          child: DeskSplit(
          hasDocument: manager.opened != null,
          selection: manager.openKey,
          listLabel: 'DAILY WORK',
          count: manager.documents.length,
          list: _Desk(manager: manager),
          sheet: manager.opened == null
                  ? _EmptySheet(loading: manager.loading)
                  : DocumentSheet(
                document: manager.opened,
                briefs: briefs,
                nav: nav,
                settings: settings,
                dirty: briefs.dirty,
                onOpenLink: manager.openLink,
                onDecide: (accept, reason) async {
                  final d = manager.opened;
                  if (d == null) return;
                  await manager.decide(d, accept: accept, reason: reason);
                },
              ),
          ),
        );
      },
    );
  }
}

class _Desk extends StatelessWidget {
  const _Desk({required this.manager});
  final DailyWorkManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final desk = manager.desk;
    final waiting = manager.awaitingSignature;
    // The newest briefing is the situation; the ones before it are history.
    final latest = desk.where((d) => d.kind == DocKind.briefing).firstOrNull;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text('DAILY WORK', style: opsHeadingStyle(context)),
                  const Spacer(),
                  Text(
                    waiting > 0
                        ? '$waiting AWAITING SIGNATURE'
                        : manager.loading
                        ? 'READING…'
                        : '${desk.length} DOCUMENTS',
                    style: opsLabelStyle(context).copyWith(
                      color: waiting > 0 ? cs.primary : null,
                    ),
                  ),
                ],
              ),
              if (manager.problem != null) ...[
                const SizedBox(height: 6),
                OpsError(manager.problem!),
              ],
            ],
          ),
        ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: ListView.builder(
            // Newest first, a page at a time; the last row asks for more.
            // Rows carry their own rule.
            itemCount: desk.length > manager.shown ? manager.shown + 1 : desk.length,
            itemBuilder: (context, i) {
              if (i == manager.shown) {
                final left = desk.length - manager.shown;
                return InkWell(
                  onTap: manager.showMore,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: Sp.l, horizontal: Sp.xl),
                    child: Text(
                      'SHOW EARLIER · $left MORE',
                      style: opsLabelStyle(context).copyWith(color: AppTheme.ink(context)),
                    ),
                  ),
                );
              }
              final d = desk[i];
              final briefing = d.kind == DocKind.briefing;
              return DocumentRow(
                document: d,
                selected: manager.isOpen(d),
                onTap: () => manager.select(d),
                current: briefing && identical(d, latest),
                superseded: briefing && !identical(d, latest),
              );
            },
          ),
        ),
      ],
    );
  }
}


/// Which run the paperwork is from, when the project has had more than
/// one: the session's stamp and how it ended, opening a menu of the rest.
/// The live one is chosen by default; history is one pick away.
/// Delete is permanent: the session's files, its registry lines, its library
/// record. Ask once, in the ops voice, before doing it.
/// worker by model. Same line a person would put on a job board.

/// The right-hand side when there is no paper to open: the same frame,
/// a line saying why. The desk stays the desk when it is empty.
class _EmptySheet extends StatelessWidget {
  const _EmptySheet({required this.loading});
  final bool loading;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Text(
        loading
            ? 'Reading the day\'s paperwork…'
            : 'Nothing has arrived. When the loop runs, briefings, work orders '
                  'and change requests land here in order.',
        textAlign: TextAlign.center,
        style: Theme.of(context).textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
      ),
    );
  }
}
