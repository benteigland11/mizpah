/// A procedure in the playbook: the method a worker follows one step at a
/// time. Minted by a worker after a green gate, or written by a person;
/// improved by either. The shop's, not a task's.
class ProcedureStep {
  const ProcedureStep({required this.id, required this.title, required this.do_, this.procedure});
  final String id;
  final String title;

  /// The imperative: do this.
  final String do_;

  /// A linked procedure: walking this step means opening that one.
  final String? procedure;
}

class ProcedureOrigin {
  const ProcedureOrigin({required this.taskId, required this.taskTitle, required this.workOrder, required this.at});
  final String taskId;
  final String taskTitle;
  final String workOrder;
  final DateTime? at;
}

/// Use it or lose it: where a procedure stands with the library's ledger.
/// The clock is green gates. A touch (edit, use, search) buys grace; when
/// every touch has run out the procedure has decayed: hidden from workers
/// until a person revives it. Pinned or protected ones never retire.
class LibraryStanding {
  const LibraryStanding({
    this.retired = false,
    this.pinned = false,
    this.protected = false,
    this.graceLeft,
    this.retiredAt,
    this.retiredReason,
    this.touched = const {},
  });
  final bool retired;
  final bool pinned;
  final bool protected;

  /// Gates until it retires; null when it cannot (pinned, protected,
  /// retired already, or decay is off).
  final int? graceLeft;
  final int? retiredAt;

  /// `decay` or `manual`.
  final String? retiredReason;

  /// Touch kind → the gate it last happened at.
  final Map<String, int> touched;

  bool get safe => graceLeft == null;

  static const none = LibraryStanding();
}

/// The ledger's rules and clock.
class LibraryPolicy {
  const LibraryPolicy({this.enabled = false, this.grace = 50, this.clock = 0, this.weights = const {'edit': 3, 'use': 2, 'search': 1}});
  final bool enabled;

  /// Gates a search-touch lasts; use and edit last their weight times this.
  final int grace;
  final int clock;
  final Map<String, int> weights;
}

class Procedure {
  const Procedure({
    required this.id,
    required this.title,
    required this.description,
    required this.tags,
    required this.steps,
    required this.updatedAt,
    this.mintedBy,
    this.usedBy = const [],
    this.standing = LibraryStanding.none,
  });
  final String id;
  final String title;

  /// When to pick it.
  final String description;
  final List<String> tags;
  final List<ProcedureStep> steps;
  final DateTime updatedAt;

  /// The work order whose green gate filed it, when a run on this machine did.
  final ProcedureOrigin? mintedBy;

  /// Work orders that opened it since.
  final List<ProcedureOrigin> usedBy;

  final LibraryStanding standing;
  bool get retired => standing.retired;
}
