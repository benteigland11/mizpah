import 'package:flutter/material.dart';

import '../engine/host_watch.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';

/// One line at the foot of Home: what the loops hold on this machine, and
/// what the app had to catch. Quiet when nothing leaked; the reaped count
/// in the error colour when something did, because that is a widget to
/// fix, not a number to admire.
class HostStrip extends StatelessWidget {
  const HostStrip({super.key, required this.watch});
  final HostWatch watch;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: watch,
      builder: (context, _) {
        if (watch.lastScan == null && watch.error == null) return const SizedBox.shrink();
        final held = '${watch.loops} ${watch.loops == 1 ? 'LOOP' : 'LOOPS'} · '
            '${watch.marked.length} PROCESSES · ${watch.rssMb} MB';
        final caught = watch.reapedTotal == 0
            ? null
            : 'REAPED ${watch.reapedTotal} LEAKED (${watch.reapedByComm}, ${watch.reapedMb} MB) · '
                'LAST ${_ago(watch.reaped.first.at)}';
        return Container(
          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.s, Sp.xl, Sp.s),
          decoration: BoxDecoration(border: Border(top: BorderSide(color: cs.outlineVariant))),
          child: Row(
            children: [
              Text('HOST', style: opsLabelStyle(context)),
              const SizedBox(width: Sp.m),
              Expanded(
                child: Text(
                  watch.error != null ? 'UNREADABLE: ${watch.error}' : held,
                  style: opsLabelStyle(context).copyWith(color: cs.onSurfaceVariant),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (caught != null) ...[
                const SizedBox(width: Sp.m),
                Text(caught, style: opsLabelStyle(context).copyWith(color: AppTheme.removed(context))),
              ],
            ],
          ),
        );
      },
    );
  }

  String _ago(DateTime at) {
    final d = DateTime.now().difference(at);
    if (d.inSeconds < 60) return '${d.inSeconds}s AGO';
    if (d.inMinutes < 60) return '${d.inMinutes}m AGO';
    return '${d.inHours}h AGO';
  }
}
