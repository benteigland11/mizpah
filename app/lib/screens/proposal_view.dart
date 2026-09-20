import 'package:flutter/material.dart';

import '../cg/diff_line_list/diff_line_list.dart';
import '../models/brief.dart';
import '../state/brief_manager.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import '../widgets/ops.dart';

/// A proposal read as what it is: a memorandum requesting a change to the
/// brief. A sheet on the desk — header block, the argument, the change
/// stated in a sentence and shown as a diff, a decision line at the foot.
class ProposalView extends StatelessWidget {
  const ProposalView({super.key, required this.manager});
  final BriefManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final brief = manager.draft!;
    final open = manager.openProposals;
    final ix = open.indexWhere((p) => p.id == manager.viewingProposal);
    final proposal = ix >= 0
        ? open[ix]
        : brief.proposals.firstWhere((p) => p.id == manager.viewingProposal);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 32, 0),
          child: Row(
            children: [
              TextButton.icon(
                onPressed: () => manager.openProposal(null),
                icon: const Icon(Icons.arrow_back, size: 16),
                label: Text('BRIEF', style: opsLabelStyle(context)),
              ),
              const Spacer(),
              if (open.isNotEmpty) ...[
                IconButton(
                  icon: const Icon(Icons.chevron_left),
                  onPressed: ix > 0
                      ? () => manager.openProposal(open[ix - 1].id)
                      : null,
                ),
                Text(
                  '${ix + 1} OF ${open.length}',
                  style: opsLabelStyle(context),
                ),
                IconButton(
                  icon: const Icon(Icons.chevron_right),
                  onPressed: ix >= 0 && ix < open.length - 1
                      ? () => manager.openProposal(open[ix + 1].id)
                      : null,
                ),
              ],
            ],
          ),
        ),
        if (manager.error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(manager.error!, style: TextStyle(color: cs.error)),
          ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(32, 16, 32, 64),
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 760),
                child: OpsSheet(
                  child: _Memo(
                    manager: manager,
                    brief: brief,
                    proposal: proposal,
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _Memo extends StatelessWidget {
  const _Memo({
    required this.manager,
    required this.brief,
    required this.proposal,
  });
  final BriefManager manager;
  final Brief brief;
  final Proposal proposal;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final body = theme.textTheme.bodyLarge!.copyWith(
      fontSize: 17,
      color: ink,
      height: 1.55,
    );
    final when = _date(proposal.createdAt);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'MEMORANDUM',
          style: opsHeadingStyle(context).copyWith(fontSize: 24, color: ink),
        ),
        const SizedBox(height: 28),
        _HeaderRow('To', 'Head of Operations, ${brief.title}'),
        _HeaderRow('From', 'Controller'),
        _HeaderRow('Date', when),
        _HeaderRow(
          'Re',
          'Change request ${proposal.id} against brief v${brief.version}',
        ),
        const SizedBox(height: 12),
        Divider(color: cs.outline, thickness: 1.5),
        const SizedBox(height: 28),
        Text(proposal.summary, style: body),
        const SizedBox(height: 28),
        for (final e in proposal.patch.entries)
          if (!e.key.startsWith('removed_')) ...[
            _Change(
              brief: brief,
              key_: e.key,
              value: e.value,
              body: body,
              proposalPatch: proposal.patch,
            ),
            const SizedBox(height: 28),
          ],
        const SizedBox(height: 20),
        Divider(color: cs.outline, thickness: 1.5),
        const SizedBox(height: 20),
        _Decision(
          proposal: proposal,
          enabled: proposal.status == 'open' && !manager.dirty,
          dirty: manager.dirty,
          onAccept: (reason) =>
              manager.decide(proposal.id, accept: true, reason: reason),
          onReject: (reason) =>
              manager.decide(proposal.id, accept: false, reason: reason),
        ),
      ],
    );
  }

  static String _date(String iso) {
    final d = DateTime.tryParse(iso);
    if (d == null) return iso;
    const months = [
      'January',
      'February',
      'March',
      'April',
      'May',
      'June',
      'July',
      'August',
      'September',
      'October',
      'November',
      'December',
    ];
    return '${d.day} ${months[d.month - 1]} ${d.year}';
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
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: [
          SizedBox(
            width: 88,
            child: Text(
              '${k.toUpperCase()}:',
              style: opsLabelStyle(context).copyWith(fontSize: 13),
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

/// The change, stated in a sentence, then shown as a diff of the section.
class _Change extends StatelessWidget {
  const _Change({
    required this.brief,
    required this.key_,
    required this.value,
    required this.body,
    this.proposalPatch = const {},
  });
  final Brief brief;
  final String key_;
  final Object? value;
  final TextStyle body;

  /// The whole patch, for a change that reads a sibling key (a removal's
  /// text is kept beside its index once accepted).
  final Map<String, dynamic> proposalPatch;

  @override
  Widget build(BuildContext context) {
    switch (key_) {
      case 'add_need':
        return _addition(context, 'Needs', [
          for (final e in brief.needs) e.value,
        ], '$value');
      case 'add_deliverable':
        return _addition(context, 'Deliverables', [
          for (final e in brief.deliverables) e.value,
        ], '$value');
      case 'add_non_goal':
        return _addition(context, 'Non-goals', [
          for (final e in brief.nonGoals) e.value,
        ], '$value');
      case 'add_enabler':
        final en = value is Map ? (value as Map) : const {};
        return _addition(
          context,
          'Enablers',
          [for (final e in brief.enablers) '${e.id}  —  ${e.title}'],
          '${en['id']}  —  ${en['title']}',
          trailing: '${en['status'] ?? 'needed'}'.toUpperCase(),
        );
      case 'edit_need':
      case 'edit_deliverable':
      case 'edit_non_goal':
        final edit = value is Map ? (value as Map) : const {};
        final index = (edit['index'] as num?)?.toInt() ?? 0;
        final entries = _section(key_.substring(5));
        // Once accepted the brief already reads the new way; terra keeps
        // what the entry said before under `was`.
        final before = edit['was'] as String? ??
            (index >= 1 && index <= entries.length ? entries[index - 1] : '');
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _lead(context, 'Rewrite item ${_num(index)} of ', _title(key_.substring(5)), ':'),
            const SizedBox(height: 12),
            ReplaceDiff(before: before, after: '${edit['text']}', style: diffStyle(context)),
          ],
        );
      case 'remove_need':
      case 'remove_deliverable':
      case 'remove_non_goal':
        final index = (value as num?)?.toInt() ?? 0;
        final entries = _section(key_.substring(7));
        final removed = proposalPatch['removed_${key_.substring(7)}'] as String? ??
            (index >= 1 && index <= entries.length ? entries[index - 1] : '');
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _lead(context, 'Remove item ${_num(index)} from ', _title(key_.substring(7)),
                '; the items after it renumber:'),
            const SizedBox(height: 12),
            DiffLine.removed(removed, style: diffStyle(context), index: index - 1),
          ],
        );
      case 'removed_need':
      case 'removed_deliverable':
      case 'removed_non_goal':
        return const SizedBox.shrink();
      case 'mission':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _lead(context, 'Replace the ', 'Mission', ' with the following:'),
            const SizedBox(height: 12),
            ReplaceDiff(
              before: brief.mission,
              after: '$value',
              style: diffStyle(context),
            ),
          ],
        );
      default:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _lead(context, 'Note', '', ':'),
            const SizedBox(height: 8),
            Text('$value', style: body),
          ],
        );
    }
  }

  List<String> _section(String kind) => switch (kind) {
        'need' => [for (final e in brief.needs) e.value],
        'deliverable' => [for (final e in brief.deliverables) e.value],
        _ => [for (final e in brief.nonGoals) e.value],
      };

  String _title(String kind) => switch (kind) {
        'need' => 'Needs',
        'deliverable' => 'Deliverables',
        _ => 'Non-goals',
      };

  String _num(int index) => index.toString().padLeft(2, '0');

  Widget _addition(
    BuildContext context,
    String section,
    List<String> existing,
    String added, {
    String? trailing,
  }) {
    final after = existing.isEmpty
        ? ' as its first item:'
        : ', after item ${existing.length.toString().padLeft(2, '0')}:';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _lead(context, 'Add the following to ', section, after),
        const SizedBox(height: 12),
        AppendDiff(
          existing: existing,
          added: added,
          trailing: trailing,
          style: diffStyle(context),
        ),
      ],
    );
  }

  /// "Add the following to **ENABLERS**, after item 01:" — the section is
  /// the loudest word in the sentence.
  Widget _lead(BuildContext context, String pre, String section, String post) {
    final cs = Theme.of(context).colorScheme;
    return Text.rich(
      TextSpan(
        style: body.copyWith(color: cs.onSurfaceVariant),
        children: [
          TextSpan(text: pre),
          TextSpan(
            text: section.toUpperCase(),
            style: opsHeadingStyle(context)
                .copyWith(fontSize: 18, color: AppTheme.ink(context)),
          ),
          TextSpan(text: post),
        ],
      ),
    );
  }
}

/// Signature line: decide, or show what was decided.
/// The signature line: accept or reject, with an optional reason beneath
/// the change request. The reason travels with the decision (terra brief
/// accept/reject --reason) and is what the controller reads before it
/// would propose the same thing again.
class _Decision extends StatefulWidget {
  const _Decision({
    required this.proposal,
    required this.enabled,
    required this.dirty,
    required this.onAccept,
    required this.onReject,
  });
  final Proposal proposal;
  final bool enabled;
  final bool dirty;
  final void Function(String reason) onAccept;
  final void Function(String reason) onReject;

  @override
  State<_Decision> createState() => _DecisionState();
}

class _DecisionState extends State<_Decision> {
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final proposal = widget.proposal;
    final enabled = widget.enabled;
    final dirty = widget.dirty;
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final label = SizedBox(
      width: 88,
      child: Text(
        'DECISION:',
        style: opsLabelStyle(context).copyWith(fontSize: 13),
      ),
    );
    if (proposal.status != 'open') {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              label,
              Text(
                proposal.status.toUpperCase(),
                style: opsHeadingStyle(context).copyWith(color: ink),
              ),
            ],
          ),
          if (proposal.decisionReason.isNotEmpty) ...[
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.only(left: 88),
              child: Text(
                proposal.decisionReason,
                style: Theme.of(context).textTheme.bodyLarge!.copyWith(
                  color: ink,
                  fontStyle: FontStyle.italic,
                  height: 1.5,
                ),
              ),
            ),
          ],
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _reason,
          enabled: enabled,
          minLines: 2,
          maxLines: 5,
          style: Theme.of(context).textTheme.bodyLarge!.copyWith(color: ink),
          decoration: InputDecoration(
            labelText: 'REASON (OPTIONAL)',
            labelStyle: opsLabelStyle(context).copyWith(fontSize: 12),
            hintText: 'What you saw. Kept on the change request; a rejection\'s reason is why it will not be proposed again.',
            border: OutlineInputBorder(borderSide: BorderSide(color: cs.outline)),
            alignLabelWithHint: true,
          ),
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            label,
            FilledButton(
              onPressed: enabled ? () => widget.onAccept(_reason.text) : null,
          style: FilledButton.styleFrom(
            backgroundColor: AppTheme.added(context),
            foregroundColor: cs.surface,
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 18),
          ),
              child: const Text('ACCEPT'),
            ),
            const SizedBox(width: 12),
            OutlinedButton(
              onPressed: enabled ? () => widget.onReject(_reason.text) : null,
              style: OutlinedButton.styleFrom(
                foregroundColor: AppTheme.removed(context),
                side: BorderSide(color: AppTheme.removed(context)),
                padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 18),
              ),
              child: const Text('REJECT'),
            ),
            if (dirty) ...[
              const SizedBox(width: 20),
              Text(
                'SAVE YOUR BRIEF EDITS FIRST',
                style: opsLabelStyle(context).copyWith(color: cs.error),
              ),
            ],
          ],
        ),
      ],
    );
  }
}
