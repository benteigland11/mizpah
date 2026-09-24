import 'package:flutter/material.dart';

import '../engine/engine.dart';
import '../state/brief_manager.dart';
import '../state/daily_work_manager.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'document_sheet.dart';
import 'ops.dart';

/// The project's own line, under the tab strip on every one of its pages:
/// its name and state, which run is on the desk, who is on the job, and
/// the one control the state calls for (hold a live loop, resume a stopped
/// one), with the rest behind ···. Controls over the thing live with the
/// thing, not with one of its views — they sat in a menu on one line of
/// one tab before. Drawn in the paper's idiom: a rule, key labels, square
/// outlines, no fill.
class ProjectBar extends StatelessWidget {
  const ProjectBar({super.key, required this.briefs, required this.work});
  final BriefManager briefs;
  final DailyWorkManager work;

  static String _state(RunSession s) => s.running
      ? 'LIVE'
      : s.archived
          ? 'ARCHIVED'
          : (s.stop == 'completed' || s.stop == 'nothing_owed')
              ? 'COMPLETED'
              : 'STOPPED';

  static String _stamp(DateTime? t) {
    if (t == null) return '';
    final l = t.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(l.day)}.${two(l.month)} ${two(l.hour)}:${two(l.minute)}';
  }

  Future<void> _confirmDelete(BuildContext context, RunSession s) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('DELETE SESSION ${_stamp(s.startedAt)}', style: opsHeadingStyle(ctx)),
        content: const Text('Its files, journals and library record go for good. The project and its brief stay.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('DELETE')),
        ],
      ),
    );
    if (yes == true) await work.deleteSession(s.path);
  }

  static final _primary = FilledButton.styleFrom(
    padding: const EdgeInsets.symmetric(horizontal: 12),
    minimumSize: const Size(0, 30),
    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
  );

  Future<void> _memo(BuildContext context, bool live) => openMemo(context, briefs, work);

  /// The memo sheet, from the bar's button or the keyboard (Ctrl+M).
  static Future<void> openMemo(BuildContext context, BriefManager briefs, DailyWorkManager work) async {
    final live = work.anyLive;
    final tasks = [for (final t in briefs.route.tasks) if (t.status == 'in_progress') (t.id, t.title)];
    await showDialog<void>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.7),
      builder: (_) => _MemoSheet(
        live: live,
        workers: live ? tasks : const [],
        onSend: (to, text) => to == null ? work.reply(text) : work.memoToWorker(to, text),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([briefs, work]),
      builder: (context, _) {
        final theme = Theme.of(context);
        final cs = theme.colorScheme;
        final ink = AppTheme.ink(context);
        final project = briefs.briefs.where((b) => b.id == briefs.selectedId).firstOrNull;
        if (project == null) return const SizedBox.shrink();
        final sessions = work.sessions;
        final shown = sessions.where((s) => s.path == work.session).firstOrNull ?? sessions.firstOrNull;
        final status = switch (project.state) {
          'live' => 'LIVE',
          'completed' => 'COMPLETED',
          'stopped' => 'STOPPED',
          'archived' => 'ARCHIVED',
          _ => 'DRAFT',
        };
        final k = opsKeyStyle(context);
        final busy = work.working != null;
        return Container(
          height: 40,
          padding: const EdgeInsets.symmetric(horizontal: Sp.l),
          decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
          child: Row(
            children: [
              Flexible(
                child: Text(
                  project.title,
                  overflow: TextOverflow.ellipsis,
                  softWrap: false,
                  style: theme.textTheme.bodyMedium!.copyWith(color: ink, fontWeight: FontWeight.w500),
                ),
              ),
              const SizedBox(width: Sp.m),
              DocStamp(status, hot: status == 'STOPPED', sign: false, quiet: status == 'ARCHIVED'),
              // What the live run is doing, at a glance: which seat has it and where it is.
              if (work.now.isNotEmpty) ...[
                const SizedBox(width: Sp.m),
                Flexible(
                  flex: 2,
                  child: Text(
                    work.now.toUpperCase(),
                    overflow: TextOverflow.ellipsis,
                    softWrap: false,
                    style: k.copyWith(color: ink),
                  ),
                ),
              ],
              const Spacer(),
              if (work.crew.isNotEmpty) ...[
                Flexible(
                  child: Text(
                    [for (final e in work.crew.entries) '${e.key} ${e.value}'].join(' · '),
                    overflow: TextOverflow.ellipsis,
                    softWrap: false,
                    style: k,
                  ),
                ),
                const SizedBox(width: Sp.l),
              ],
              if (work.working != null) ...[
                Text('${work.working!.toUpperCase()}…', style: k.copyWith(color: cs.primary)),
                const SizedBox(width: Sp.m),
              ],
              // A memo: to Project Eval for its next briefing, or to the
              // worker on a live task, sent from here whatever page is up.
              // Things to click read as buttons — an icon and a word, a
              // fill on the one that matters — never as the bordered caps
              // of a stamp, which is a state, not a control.
              if (shown != null) ...[
                TextButton.icon(
                  onPressed: busy ? null : () => _memo(context, work.anyLive),
                  icon: const Icon(Icons.edit_outlined, size: 15),
                  label: const Text('MEMO'),
                  style: TextButton.styleFrom(
                    foregroundColor: ink,
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    minimumSize: const Size(0, 30),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                ),
                const SizedBox(width: Sp.s),
              ],
              // The one control the state calls for. HOLD acts on whatever
              // is live, whichever run the desk shows; RESUME relaunches the
              // shown run, and only when nothing is live.
              if (work.anyLive)
                FilledButton.icon(
                  onPressed: busy || work.holding ? null : work.hold,
                  icon: work.holding
                      ? const SizedBox.square(dimension: 13, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.pause, size: 15),
                  label: Text(work.holding ? 'HOLDING' : 'HOLD'),
                  style: _primary,
                )
              else if (shown != null && !shown.archived)
                FilledButton.icon(
                  onPressed: busy ? null : work.resume,
                  icon: work.working == 'Resuming'
                      ? const SizedBox.square(dimension: 13, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.play_arrow, size: 15),
                  label: Text(work.working == 'Resuming' ? 'RESUMING' : 'RESUME'),
                  style: _primary,
                ),
              if (shown != null) ...[
                const SizedBox(width: 4),
                // The rest: which run the desk shows (a project keeps every
                // run), and what can be done with the shown one when it is
                // not live.
                PopupMenuButton<String>(
                  tooltip: 'More',
                  padding: EdgeInsets.zero,
                  icon: Icon(Icons.more_horiz, size: 18, color: ink),
                  onSelected: (v) {
                    if (v == 'archive') {
                      work.archiveSession(shown.path);
                    } else if (v == 'delete') {
                      _confirmDelete(context, shown);
                    } else {
                      work.chooseSession(v);
                    }
                  },
                  itemBuilder: (_) => [
                    if (sessions.length > 1) ...[
                      PopupMenuItem(enabled: false, value: '', child: Text('SHOW RUN', style: k)),
                      for (final s in sessions)
                        PopupMenuItem(
                          value: s.path,
                          child: Text(
                            '${_stamp(s.startedAt)}  ${_state(s)}${s.path == shown.path ? '  · shown' : ''}',
                            style: k.copyWith(color: s.path == shown.path ? ink : null),
                          ),
                        ),
                      if (!shown.running) const PopupMenuDivider(),
                    ],
                    if (!shown.running) ...[
                      if (!shown.archived) PopupMenuItem(value: 'archive', child: Text('ARCHIVE THIS RUN', style: k)),
                      PopupMenuItem(value: 'delete', child: Text('DELETE THIS RUN…', style: k.copyWith(color: AppTheme.removed(context)))),
                    ],
                  ],
                ),
              ],
            ],
          ),
        );
      },
    );
  }
}

/// The memo sheet: a To line — Project Eval, or a worker on a live task —
/// and the note. Sent as one memo; it goes on record in Daily work.
class _MemoSheet extends StatefulWidget {
  const _MemoSheet({required this.live, required this.workers, required this.onSend});
  final bool live;
  final List<(String, String)> workers;
  final Future<void> Function(String? to, String text) onSend;

  @override
  State<_MemoSheet> createState() => _MemoSheetState();
}

class _MemoSheetState extends State<_MemoSheet> {
  final _text = TextEditingController();
  String? to; // null: Project Eval
  bool busy = false;
  String? error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final t = _text.text.trim();
    if (t.isEmpty) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.onSend(to, t);
      if (mounted) Navigator.pop(context);
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
    final k = opsKeyStyle(context);
    return Dialog(
      backgroundColor: Colors.transparent,
      child: OpsSheet(
        maxWidth: 640,
        padding: const EdgeInsets.fromLTRB(40, 32, 40, 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('MEMO', style: opsHeadingStyle(context)),
            const SizedBox(height: Sp.l),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(width: 56, child: Padding(padding: const EdgeInsets.only(top: 6), child: Text('TO', style: k))),
                Expanded(
                  child: Wrap(
                    spacing: Sp.s,
                    runSpacing: Sp.s,
                    children: [
                      _ToChip(label: 'Project Eval', on: to == null, onTap: () => setState(() => to = null)),
                      for (final (id, title) in widget.workers)
                        _ToChip(label: 'Worker · $title', on: to == id, onTap: () => setState(() => to = id)),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: Sp.s),
            Padding(
              padding: const EdgeInsets.only(left: 56),
              child: Text(
                to == null
                    ? (widget.live
                        ? 'Read at the next briefing; the briefing that follows is the answer.'
                        : 'Read at the next briefing. The loop is stopped: sending resumes it to answer.')
                    : 'Delivered at the worker\'s next boundary, as a word from the person running the loop.',
                style: k,
              ),
            ),
            const SizedBox(height: Sp.l),
            TextField(
              controller: _text,
              autofocus: true,
              minLines: 4,
              maxLines: 12,
              style: theme.textTheme.bodyLarge!.copyWith(color: ink, height: 1.45),
              decoration: InputDecoration(
                border: OutlineInputBorder(borderSide: BorderSide(color: cs.outline)),
                hintText: 'What you want it to know.',
                hintStyle: TextStyle(color: cs.outline, fontStyle: FontStyle.italic),
              ),
            ),
            if (error != null) ...[
              const SizedBox(height: Sp.s),
              OpsError(error!),
            ],
            const SizedBox(height: Sp.l),
            Row(
              children: [
                TextButton(onPressed: busy ? null : () => Navigator.pop(context), child: const Text('DISCARD')),
                const Spacer(),
                FilledButton(onPressed: busy ? null : _send, child: Text(busy ? 'SENDING…' : 'SEND')),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ToChip extends StatelessWidget {
  const _ToChip({required this.label, required this.on, required this.onTap});
  final String label;
  final bool on;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          border: Border.all(color: on ? ink : cs.outlineVariant, width: on ? 1.2 : 1),
          borderRadius: BorderRadius.circular(2),
        ),
        child: Text(label, style: AppTheme.mono.copyWith(fontSize: 12, color: on ? ink : cs.onSurfaceVariant)),
      ),
    );
  }
}

