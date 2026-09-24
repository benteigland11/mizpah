import 'package:flutter/material.dart';

import '../../cg/segmented_capacity_bar/segmented_capacity_bar.dart';
import '../../models/budget.dart';
import '../../state/budget_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/ops.dart';

/// The points ledger. The budget is the number people tune most, so it is
/// the big number; beneath it the bar (spent · committed · idle reserve ·
/// available), the accounts (each sector and the free pool), and every
/// task as a line item under the account it draws on.
class BudgetScreen extends StatelessWidget {
  const BudgetScreen({super.key, required this.manager});
  final BudgetManager manager;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final l = manager.ledger;
        if (manager.loading || l.isEmpty) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(32, 28, 32, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('BUDGET', style: opsHeadingStyle(context)),
                const SizedBox(height: Sp.m),
                Text(
                  manager.loading
                      ? 'Opening the ledger…'
                      : 'No budget on the brief and no work routed.',
                  style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                ),
              ],
            ),
          );
        }
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(40, 28, 40, 48),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: AppTheme.contentWidth),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _Headline(ledger: l),
                  const SizedBox(height: Sp.xl),
                  _Accounts(ledger: l),
                  if (l.drift.isNotEmpty) ...[
                    const SizedBox(height: Sp.xxl),
                    const OpsLabel('OFF BASELINE'),
                    const SizedBox(height: Sp.s),
                    const _LineHeads(),
                    for (final x in l.drift) _LineRow(line: x),
                  ],
                  const SizedBox(height: Sp.xxl),
                  const OpsLabel('LINE ITEMS'),
                  const SizedBox(height: Sp.s),
                  const _LineHeads(),
                  for (final s in [...l.sectors, l.freePool])
                    if (s.lines.isNotEmpty) ...[
                      _AccountHead(sector: s),
                      for (final x in s.lines) _LineRow(line: x),
                    ],
                  if (l.notes.isNotEmpty) ...[
                    const SizedBox(height: Sp.xxl),
                    const OpsLabel('NOTES'),
                    const SizedBox(height: Sp.m),
                    Text(
                      l.notes,
                      style: theme.textTheme.bodyLarge!.copyWith(
                        color: AppTheme.ink(context),
                        height: 1.5,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Big number, then the bar and the four figures that make it up.
class _Headline extends StatelessWidget {
  const _Headline({required this.ledger});
  final BudgetLedger ledger;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l = ledger;
    final idle = l.idleReserve;
    final available = l.available;
    final big = AppTheme.mono.copyWith(
      fontSize: 44,
      fontWeight: FontWeight.w700,
      color: l.over ? cs.error : AppTheme.ink(context),
      height: 1,
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text('BUDGET', style: opsHeadingStyle(context)),
            const Spacer(),
            if (l.locked)
              Padding(
                padding: const EdgeInsets.only(right: Sp.l, bottom: 4),
                child: Text('BASELINE LOCKED', style: opsLabelStyle(context)),
              ),
            Text(l.budget?.toString() ?? '—', style: big),
            Padding(
              padding: const EdgeInsets.only(left: 8, bottom: 6),
              child: Text('PTS', style: opsLabelStyle(context)),
            ),
          ],
        ),
        const SizedBox(height: Sp.l),
        SegmentedCapacityBar(
          segments: [
            CapacitySegment(l.spent, cs.primary),
            CapacitySegment(l.committed, cs.onSurface),
            CapacitySegment(idle, cs.outlineVariant),
          ],
          capacity: l.budget,
          height: 14,
          outlineColor: cs.outline,
          overrunColor: cs.error,
        ),
        const SizedBox(height: Sp.l),
        OpsStrip(
          children: [
            OpsStat('SPENT', _n(l.spent), accent: true),
            OpsStat('COMMITTED', _n(l.committed)),
            OpsStat('IDLE RESERVE', _n(idle)),
            OpsStat(
              l.over ? 'OVER BY' : 'AVAILABLE',
              _n(available?.abs(), hot: l.over),
            ),
            OpsStat('RESERVED', _n(l.reserved)),
            OpsStat('FREE POOL', _n(l.free)),
            OpsStat('RETURNED', _n(l.cancelled)),
          ],
        ),
      ],
    );
  }
}

Widget _n(int? v, {bool hot = false}) => Builder(
  builder: (context) {
    final cs = Theme.of(context).colorScheme;
    return Text(
      v?.toString() ?? '—',
      style: AppTheme.mono.copyWith(
        fontSize: 18,
        fontWeight: FontWeight.w700,
        color: hot ? cs.error : cs.onSurface,
      ),
    );
  },
);

/// One row per account: sector reserves with their headroom, then the
/// free pool. This is where "how much is committed, and where" lives.
class _Accounts extends StatelessWidget {
  const _Accounts({required this.ledger});
  final BudgetLedger ledger;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final k = opsKeyStyle(context);
    Widget h(String t, {TextAlign a = TextAlign.right}) =>
        SizedBox(width: 96, child: Text(t, style: k, textAlign: a));
    final rows = [
      ...ledger.sectors,
      SectorLedger(
        id: '',
        title: 'Free pool (unsectored)',
        reserved: ledger.free,
        lines: ledger.freePool.lines,
      ),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const OpsLabel('ACCOUNTS'),
        const SizedBox(height: Sp.s),
        Container(
          padding: const EdgeInsets.only(bottom: 6),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: cs.outline, width: 1.5)),
          ),
          child: Row(
            children: [
              Expanded(child: Text('ACCOUNT', style: k)),
              h('CAP'),
              h('SPENT'),
              h('COMMITTED'),
              h('PLANNED'),
              h('HEADROOM'),
              h('TASKS'),
            ],
          ),
        ),
        for (final s in rows) _AccountRow(sector: s, isPool: s.id.isEmpty),
      ],
    );
  }
}

class _AccountRow extends StatelessWidget {
  const _AccountRow({required this.sector, required this.isPool});
  final SectorLedger sector;
  final bool isPool;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final s = sector;
    final headroom = s.reserved == null ? null : s.reserved! - s.planned;
    final overCap = headroom != null && headroom < 0;
    final mono = AppTheme.mono.copyWith(fontSize: 14, color: cs.onSurface);
    Widget c(String t, {TextStyle? st}) =>
        SizedBox(width: 96, child: Text(t, style: st ?? mono, textAlign: TextAlign.right));
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                if (!isPool)
                  Padding(
                    padding: const EdgeInsets.only(right: Sp.m),
                    child: Text(s.id, style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
                  ),
                Flexible(
                  child: Text(
                    s.title,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyLarge!.copyWith(
                      color: AppTheme.ink(context),
                      fontStyle: isPool ? FontStyle.italic : null,
                    ),
                  ),
                ),
              ],
            ),
          ),
          c(s.reserved?.toString() ?? '—'),
          c('${s.spent}', st: mono.copyWith(color: cs.primary, fontWeight: FontWeight.w700)),
          c('${s.committed}', st: mono.copyWith(fontWeight: FontWeight.w700)),
          c('${s.planned}', st: mono.copyWith(color: cs.onSurfaceVariant)),
          c(
            headroom?.toString() ?? '—',
            st: mono.copyWith(color: overCap ? cs.error : cs.onSurfaceVariant, fontWeight: overCap ? FontWeight.w700 : null),
          ),
          c('${s.lines.length}', st: mono.copyWith(color: cs.onSurfaceVariant)),
        ],
      ),
    );
  }
}

const _wPts = 64.0, _wStatus = 100.0, _wBucket = 90.0, _wPhase = 90.0;

class _LineHeads extends StatelessWidget {
  const _LineHeads();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final k = opsKeyStyle(context);
    Widget h(String t, double w, {TextAlign a = TextAlign.left}) =>
        SizedBox(width: w, child: Text(t, style: k, textAlign: a));
    return Container(
      padding: const EdgeInsets.only(bottom: 6),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outline, width: 1.5)),
      ),
      child: Row(
        children: [
          Expanded(child: Text('TASK', style: k)),
          h('PHASE', _wPhase),
          h('CLASS', _wBucket),
          h('STATUS', _wStatus),
          h('PLAN', _wPts, a: TextAlign.right),
          h('PTS', _wPts, a: TextAlign.right),
        ],
      ),
    );
  }
}

class _AccountHead extends StatelessWidget {
  const _AccountHead({required this.sector});
  final SectorLedger sector;

  @override
  Widget build(BuildContext context) {
    final s = sector;
    return Padding(
      padding: const EdgeInsets.fromLTRB(0, Sp.l, 0, 2),
      child: Row(
        children: [
          Text(s.title.toUpperCase(), style: opsLabelStyle(context)),
          const Spacer(),
          Text(
            '${s.spent} spent · ${s.committed} committed'
            '${s.reserved != null ? ' · cap ${s.reserved}' : ''}',
            style: opsKeyStyle(context),
          ),
        ],
      ),
    );
  }
}

class _LineRow extends StatelessWidget {
  const _LineRow({required this.line});
  final BudgetLine line;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final x = line;
    final dim = x.status == 'cancelled';
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: dim ? cs.onSurfaceVariant : cs.onSurface,
      decoration: dim ? TextDecoration.lineThrough : null,
    );
    final grey = mono.copyWith(color: cs.onSurfaceVariant);
    Widget c(String t, double w, {TextStyle? st, TextAlign a = TextAlign.left}) =>
        SizedBox(width: w, child: Text(t, style: st ?? grey, textAlign: a, overflow: TextOverflow.ellipsis));
    final statusStyle = switch (x.status) {
      'done' => grey.copyWith(color: cs.primary),
      'blocked' => grey.copyWith(color: cs.error, fontWeight: FontWeight.w700),
      'in_progress' => grey.copyWith(color: cs.onSurface, fontWeight: FontWeight.w700),
      _ => grey,
    };
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                SizedBox(
                  width: 210,
                  child: Text(x.id, style: mono, overflow: TextOverflow.ellipsis),
                ),
                Expanded(
                  child: Text(
                    x.title,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium!.copyWith(
                      color: dim ? cs.onSurfaceVariant : AppTheme.ink(context),
                      decoration: dim ? TextDecoration.lineThrough : null,
                    ),
                  ),
                ),
              ],
            ),
          ),
          c(x.phase, _wPhase),
          c(x.bucket, _wBucket),
          c(x.status.replaceAll('_', ' '), _wStatus, st: statusStyle),
          c(x.planPoints?.toString() ?? '—', _wPts, a: TextAlign.right),
          c(
            '${x.points}',
            _wPts,
            a: TextAlign.right,
            st: mono.copyWith(
              fontWeight: FontWeight.w700,
              color: x.drifted ? cs.error : (dim ? cs.onSurfaceVariant : cs.onSurface),
            ),
          ),
        ],
      ),
    );
  }
}
