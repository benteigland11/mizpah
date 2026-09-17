"""Inspect the isolation contract and persist a workspace without launching a service."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sandboxed_shell_execution import ShellConfig, ShellLimits, SandboxedShell, edit_workspace_file, read_workspace_file, write_workspace_file

limits = ShellLimits(1024**3, 16*1024**2, 8*1024**2, 65536, 8192, 32, 100, 10, 5, 1000)
with tempfile.TemporaryDirectory() as scratch:
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', scratch, limits))
    command = shell.command_argv(scratch, 'example-unit')
    assert '--unshare-all' in command and '--bind' not in command
    snapshot = write_workspace_file(b'', 'notes.txt', b'One continuing investigation.', byte_limit=1024, file_limit=10)
    print(read_workspace_file(snapshot, 'notes.txt', byte_limit=1024, file_limit=10).decode())
    snapshot, report = edit_workspace_file(snapshot, 'notes.txt', 'continuing', 'completed', byte_limit=1024, file_limit=10)
    print(report, read_workspace_file(snapshot, 'notes.txt', byte_limit=1024, file_limit=10).decode())
