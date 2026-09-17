# Sandboxed shell execution

Commands run in disposable Linux namespaces with a read-only runtime and bounded
writable workspace and temporary filesystems. Only a validated workspace archive
persists between commands. Archive readers never follow symlinks on the host.

An identified, completed command whose outgoing archive fails validation returns
`status="workspace_rejected"`. Its `workspace` is the exact previous snapshot;
all changes from that command are discarded. Bounded stdout, stderr and exit
status remain available, while `output_files` is empty because new log files were
also discarded. Treat this as a tool error and let the caller choose its next
action. Do not infer successful persistence from exit code zero alone.

Malformed, unidentified or incomplete transport remains `status="interrupted"`.
The caller must not automatically replay an uncertain action. Workspace
validation still rejects absolute links, parent traversal and unsupported entries.
