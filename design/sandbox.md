# Sandbox on three platforms

Status: design, not started (2026-09-23). Owner: open.

Mizpah ships a Flutter app so it can run on Linux, macOS and Windows. The app is portable today; the
engine is not, because every agent command runs in a sandbox built from Linux-only parts. This note
says what the sandbox guarantees, how it does it now, and how to give it one interface with a
backend per platform. The first public release is Linux only; this is the work that lifts that.

Code referred to: `SSE` is
`engine/harness/cg/infra_sandboxed_shell_execution_python/src/sandboxed_shell_execution.py`.
Line numbers are from the working tree on 2026-09-23 and will drift.

## What other harnesses do

Interactive coding agents have converged on a native sandbox per OS (check their current docs
before relying on the details):

- Claude Code (sandbox-runtime) and OpenAI Codex CLI: bubblewrap or Landlock+seccomp on Linux,
  Seatbelt (`sandbox-exec`) on macOS, WSL2 on Windows (Codex also has an experimental native one).
- Gemini CLI: Seatbelt profiles on macOS, otherwise a container.
- Hosted agents (OpenHands, Devin, cloud Codex and Claude Code): a container or VM per task.

They share a shape: the agent works in the real project directory with writes confined to it, the
network is off or goes through a local allowlisting proxy, and resource limits are mostly absent
because a person is watching. Mizpah runs unattended for hours, so it also caps memory, processes
and lifetime, hardens syscalls, runs long-lived services and keeps workspace snapshots. Those
extras are what tie it to systemd and Linux namespaces today.

## The guarantees (the policy)

These are what a backend must provide. They are the contract; the mechanisms below are one way to
meet it.

1. **Filesystem.** The command sees: the workspace (read-write), declared toolchains and binds
   (read-only), a private temp dir, and nothing else of the host. Declared state dirs and cache dirs
   behave as today (see "Workspace modes").
2. **Refusals.** `refused_paths` and `refused_patterns` reject a command before it runs. This is
   plain text matching in SSE:921-930 and is already platform-neutral.
3. **Network.** One of: none; shared host network (`share_network`); or private, where the only
   way out is an HTTP(S) proxy on the host that admits `allowed_domains` (and subdomains), always
   refuses private and loopback addresses, and logs every decision to `egress.jsonl`. Programs that
   ignore the proxy variables have no network. `egress_proxy.py` is stdlib and portable except for
   its AF_UNIX listener.
4. **Limits.** Memory, process count, CPU share, command timeout, output size, workspace size,
   file count. On timeout or breach, the whole process tree dies and the result says why
   (`timeout`, `output_limit`, OOM reported as an error).
5. **Hardening.** No privilege gain (setuid), no debugging other processes, no raw sockets, no
   kernel modules. Today via systemd seccomp properties (`HARDENING_PROPERTIES`, SSE:72).
6. **Services.** Up to `maximum_services` long-running processes per shell, with lifetime, memory
   and process caps, a capped log, and reachability from commands on loopback. Requested from inside
   with `svc` (a file-based request channel, already portable).
7. **Results.** One `ShellResult` shape and status set on every platform.

## How Linux does it today

| Guarantee | Mechanism | Where |
|---|---|---|
| Filesystem | bubblewrap: `runtime_root` as `/usr`, workspace at `/work`, `/tmp` tmpfs, `/input`, `/runner`, `/svc`, root remounted read-only, `--clearenv` | `_isolation_argv` SSE:739-778, `_work_argv` SSE:780-805 |
| Limits | `systemd-run --user` transient scope per command: `MemoryMax`, `TasksMax`, `CPUQuota`, `RuntimeMaxSec`, `KillMode=control-group`; OOM read back via `systemctl show` | SSE:829-835, 953, 962 |
| Hardening | systemd `NoNewPrivileges`, `SystemCallFilter`, `RestrictAddressFamilies`, etc. | SSE:72 |
| Network | holder process from `unshare --net`, `socat` bridge inside via `nsenter` to the host proxy's unix socket | `_ensure_network` SSE:636-705 |
| Services | transient `isolated-service-*` units, workspace mirror per service | SSE:846-872, 1085-1152 |
| Process tree | `sandbox_worker.py` must be PID 1 in its namespace and reaps with `kill(-1, SIGKILL)` | `sandbox_worker.py:20, 115` |

What is already platform-neutral: the tar and `DirectoryWorkspace` functions, the request/response
JSON protocol, `svc`, the egress proxy logic, refusals, output collection. `command_argv()` and
`service_argv()` are already public and mocked in tests. What is not: `ShellConfig` requires
`bwrap`, `systemd_run` and `systemctl`; virtual paths `/work`, `/svc`, `/runner`, `/input` are
constants; `run`, `start_service` and `_ensure_network` call `subprocess` directly with no seam.

## Proposed shape

Split `SandboxedShell` into the policy and a backend.

```
SandboxPolicy            what the command may see and do (today's ShellConfig minus the Linux
                         binaries): binds, environment, network mode + allowlist, limits,
                         services limits, refusals, workspace mode, state/cache/scratch dirs
SandboxedShell(policy, backend)
                         everything platform-neutral: refusals, workspace packing and
                         validation, svc request handling, output collection, ShellResult
Backend (protocol)
    prepare(policy) -> None                 check the host can honour the policy; say what's missing
    launch(request_dir, workspace, detached) -> Completed   run one command under the policy
    start_service(name, ...) / stop / state
    open_network(policy) / close_network()
    workspace_path -> str                   where the workspace appears to the command
```

Backends: `LinuxBwrap` (today's code moved behind the protocol), `MacSeatbelt`, `WindowsWsl` then
`WindowsNative`, and a `Null` backend for tests that need a shell but not isolation.

The session identity in `focused_agent_session.py:471-498` stores `asdict(shell.config)` and refuses
to reopen on any difference. Keep backend fields out of that identity, or saved sessions stop
reopening after the split.

## Retiring `/work`

Linux can put the workspace at a fixed `/work`; Seatbelt and Windows cannot remap paths. The
workspace must become "wherever it really is", named by a variable (`MIZPAH_WORK`, and `HOME` for
tools that want one). Linux may keep mounting at `/work` for a transition, but nothing may depend on
the literal path.

What depends on it (2026-09-23 counts):

- **Sandbox widget.** `WORKSPACE_MOUNT` (SSE:228), the reserved set (SSE:186), `_name()` rewriting
  `/work/x` to `x` (SSE:243), `HOME=/work` (SSE:772), `--chdir /work`.
- **Harness session.** `focused_agent_session.py:798, 805, 998` (scratch mapping, protected-path
  strip). It also writes the private `shell._directory` (718, 836) and imports `_name`: make both
  public API first.
- **Engine.** 30 mentions in `engine/mizpah/src`. Load-bearing:
  - `worker.py:1822` sets `XDG_DATA_HOME=/work/.playbook`.
  - `worker.py:797` parses `cartograph create` output with `.split('/work/')`.
  - `worker.py:1643` special-cases `cd /work && playbook create`.
  - `deputy.py:84-85, 141-142, 194-195`.
  - `bases.py:90, 140` refuse shebangs containing `/work/`.
- **Prompts.** 19 mentions in 7 files. `policy_worker.md:3` tells the worker it works in `/work`;
  `glossary.md:59` defines Workspace as `/work` (and is composed into every seat);
  `deputy/environment_deputy.md` rewrites venv shebangs with a GNU `sed -i`.
- **Procedure store.** 54 mentions in 11 of 282 procedures in `~/.local/share/playbook/procedures`,
  36 of them in the framework procedure `mizpah-build-environment` (`engine/playbook/playbook/ops.py:833`).
  Every gym carries a copy under `.playbook/`. The library is the loop's, not ours: do not hand-edit
  learned procedures. Change the framework procedure in the repo, and let the loop revise the others
  when they fail on a new platform.
- **Terra.** Nothing reads it, but every run records `build_from.cwd` (`probe_run.py:132`); about
  3,800 existing records say `/work`. Harmless; do not migrate.
- **App.** One doc comment and two tests expecting `Looked through /work`
  (`app/test/deputy_plain_test.dart`, `deputy_activity_test.dart`).
- **Tests.** `test_bind_mode.py`, `test_deputy.py`, `test_episodes.py`, `casebook/tests`.

Also: `XDG_DATA_HOME` only moves the playbook store on Linux. `infra_app_paths_python`
(`app_paths.py:34-57`) uses `~/Library` on macOS and `%APPDATA%` on Windows, so the redirect needs an
explicit store-path variable instead.

## Backends

### Linux (bubblewrap)

Today's code behind the protocol, plus one change: **make systemd optional.** Many Linux hosts lack
a user systemd with cgroup delegation (containers, CI runners, some distros). Without it, apply
limits with `prlimit`/`setrlimit` (address space, processes, CPU time) and a process-group timeout,
and apply seccomp another way (bubblewrap's `--seccomp` with a compiled filter, or skip it and say
so in `prepare`). This also lets CI run the live sandbox tests instead of skipping them.

Worth considering while there: `pasta` or `slirp4netns` in place of the
`unshare`/`nsenter`/`socat` dance, only if the current network path causes trouble.

### macOS (Seatbelt)

- **Filesystem:** a generated `sandbox-exec` profile: deny by default, allow reads of the system,
  the toolchains and binds, allow writes to the workspace, temp dir and scratch. Real paths.
- **Network:** a profile allowing outbound only to `localhost:<proxy_port>`, with the proxy on the
  host listening on TCP loopback (Seatbelt cannot bridge a unix socket in; bind the proxy to a
  random port and pass it). Shared network is a profile that allows outbound.
- **Limits:** `setrlimit` in a small launcher (processes, file size, CPU), a process-group timeout,
  and memory watched by polling RSS (no memory cgroup on macOS). Weaker than Linux: say so.
- **Hardening:** Seatbelt denies `process-info`/`mach` lookups outside the tree; no setuid is
  enforced by the profile.
- **Process tree:** no PID namespace, so `sandbox_worker.py`'s PID 1 check and `kill(-1)` become a
  new process group and `killpg`.
- **Services:** the same launcher, detached, tracked by pid and process group.
- **Caveat:** `sandbox-exec` is marked deprecated by Apple but is what Chrome, Bazel and the agent
  CLIs use. Watch for its removal.
- **Userland:** BSD tools (`sed -i ''`, `stat -f`). Prompts must not teach GNU-only forms, or the
  worker needs GNU coreutils on PATH (Homebrew), declared as a toolchain bind.

### Windows

**Phase 1: WSL2.** Run the engine inside a WSL2 distro with systemd enabled; the Linux backend works
there unchanged. The Windows app starts it (`wsl.exe -d <distro> ...`) or connects to it through the
existing RPC desk (`app/lib/engine/rpc_server.dart`, `remote_engine.dart`). Cost to the user: enable
WSL2 once. Projects on the Windows side are reachable as `/mnt/c/...` but slow; prefer projects inside
the distro.

**Phase 2: native.** A restricted token or AppContainer for filesystem (grant the workspace, deny
the rest by ACL), a Job Object for memory, process count, CPU and kill-on-close, and the proxy for
network (AppContainer network capability off, loopback exemption for the proxy port). The harder
problem is not isolation but the shell: the worker's tool is `bash`, prompts and procedures are
POSIX. Options: ship Git Bash/MSYS2 as the toolchain, or give Windows workers PowerShell and accept a
separate family of procedures. Decide this before building phase 2.

## Other Linux assumptions the backends will hit

- `/usr/bin/<shell>`, `/usr/bin/python3`, `runtime_root` treated as `/usr` with `lib64`
  (SSE:761-762, 842, 869, 871, 940).
- Bind syntax `source:target` splits on `:` (SSE:109): breaks on `C:\`.
- `fcntl.flock` in `loop.py:702`, `worker.py:600`, `focused_agent_session.py:597`: use a portable lock
  (`msvcrt.locking` on Windows, or a lock-file widget).
- `ops.py`: `systemctl --user` for model units and service sweeps (303-306, 361, 375), `/proc` for
  leak detection (390-460), `notify-send` default. These are host operations, not sandbox, but the
  engine will not run without a portable equivalent or a clean "not available here".
- `prctl(PR_SET_PDEATHSIG)` (SSE:649), `pgrep -f` (`loop.py:228`), `cp -a --reflink=auto`
  (`bases.py:212`), venv layout `bin/` and `lib/python3*/site-packages` vs `Scripts\` and `Lib\`
  (`bases.py:247-256`).
- `worker.py:2020` refuses `systemctl`/`systemd-run`/`loginctl` in commands: extend the refusal list
  per platform.

## Conformance suite

Turn the widget's tests into one suite every backend must pass:

- **Portable already** (run on every platform, no isolation needed): workspace round-trip and path
  refusals, tar validation, edit/read functions, refusals, log capping, egress allowlist, service
  request handling, `DirectoryWorkspace` operations. See
  `tests/test_sandboxed_shell_execution.py` lines 22-38, 39, 55, 160, 211, 238, 265-389, 431, 478-519,
  575, 590.
- **Launch contract, per backend** (today they assert bwrap argv, lines 63-128, 405, 487, 531, 590):
  rewrite as "given this policy, the backend's launch describes these permissions".
- **Behaviour, live** (today skipped without bwrap, lines 452, 548, 620, plus
  `engine/mizpah/tests/test_bind_mode.py` and `test_bases.py:79`): write outside the workspace fails;
  a read of an unbound host path fails; an allowed domain answers and a refused one does not; the
  egress log records both; a service outlives a command and is reachable on loopback; a timeout
  kills the tree; a memory breach is reported; bind-mode state stays out of the tree.

`test_bind_mode.py` is skipped wholesale without bwrap, including tests that need no sandbox (lines
155, 190). Split those out.

## Order of work

1. Make the widget's private use public (`_name`, `shell._directory`), move the Linux code behind a
   backend protocol, no behaviour change. Conformance suite passes on Linux.
2. Replace the literal `/work` with the workspace variable in engine, prompts, framework procedure
   and tests. Linux still mounts at `/work`, so nothing changes for running gyms.
3. systemd-optional Linux backend; CI runs the live suite.
4. WSL2 path for Windows: app launches or connects to the engine in the distro.
5. macOS Seatbelt backend.
6. Native Windows, after the shell decision.

## Open questions

- **One world or three.** Procedures learned by a Linux worker say `dnf` and GNU `sed`. Do workers
  on each platform keep one library (procedures carry platform notes, the loop revises what fails),
  or does the store split by platform? Widgets are code in a language and should transfer either way.
- **Is macOS memory limiting by polling good enough** for unattended runs, or does macOS need a VM
  backend (Lima, Apple Virtualization) for heavy gyms?
- **Windows shell:** Git Bash or PowerShell.
- **Does WSL2 count as "Windows support"** in release notes, or only phase 2?
