import 'package:flutter/material.dart';

import '../../models/document.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../state/work_orders_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/desk_split.dart';
import '../../widgets/document_sheet.dart';

/// The folder. Every work order the route has issued, one sheet each,
/// tabbed by where it stands: in progress, queued, blocked, closed. The
/// same sheet the desk shows — here it is filed, not arriving.
class WorkOrdersScreen extends StatelessWidget {
  const WorkOrdersScreen({
    super.key,
    required this.manager,
    required this.briefs,
    required this.nav,
    required this.settings,
  });
  final WorkOrdersManager manager;
  final BriefManager briefs;
  final AppNav nav;
  final AppSettings settings;

  static const _groups = [
    ('IN PROGRESS', ['IN PROGRESS']),
    ('QUEUED', ['QUEUED']),
    ('BLOCKED', ['BLOCKED', 'ABORTED', 'GATE RED', 'STOPPED']),
    ('CLOSED', ['GATE GREEN', 'CLOSED']),
    ('CANCELLED', ['CANCELLED']),
  ];

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final orders = manager.orders;
        final open = orders
            .where((d) => d.status != 'GATE GREEN' && d.status != 'CLOSED' && d.status != 'CANCELLED')
            .length;
        return DeskSplit(
          hasDocument: manager.opened != null,
          selection: manager.opened,
          listLabel: 'WORK ORDERS',
          count: open,
          list: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('WORK ORDERS', style: opsHeadingStyle(context)),
                        const Spacer(),
                        Text(
                          manager.loading
                              ? 'OPENING…'
                              : '$open OPEN · ${orders.length} ISSUED',
                          style: opsLabelStyle(context).copyWith(
                            color: open > 0 ? cs.primary : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(
                    child: ListView(
                      children: [
                        for (final (label, stamps) in _groups)
                          ..._group(
                            context,
                            label,
                            stamps,
                            orders.where((d) => stamps.contains(d.status)).toList(),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
          sheet: manager.opened == null
                  ? Center(
                      child: Text(
                        manager.loading
                            ? 'Opening the folder…'
                            : 'Nothing issued yet. The controller writes work '
                                  'orders when it routes the brief.',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.bodyLarge!.copyWith(
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    )
                  : DocumentSheet(
                      document: manager.opened,
                      briefs: briefs,
                      nav: nav,
                      settings: settings,
                    ),
        );
      },
    );
  }

  List<Widget> _group(
    BuildContext context,
    String label,
    List<String> stamps,
    List<InboxDocument> docs,
  ) {
    if (docs.isEmpty) return const [];
    final cs = Theme.of(context).colorScheme;
    return [
      Container(
        padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, 4),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(right: Sp.s),
              decoration: BoxDecoration(
                color: DocStamp.colorFor(context, stamps.first),
                shape: BoxShape.circle,
              ),
            ),
            Text(
              '$label · ${docs.length}',
              style: opsLabelStyle(context).copyWith(
                color: DocStamp.colorFor(context, stamps.first),
              ),
            ),
          ],
        ),
      ),
      for (final d in docs)
        DocumentRow(
          document: d,
          selected: identical(d, manager.opened),
          onTap: () => manager.select(d),
        ),
    ];
  }
}
