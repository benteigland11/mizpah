# Security

Mizpah runs model-written commands on your machine for hours at a time. The sandbox is what keeps
that safe: each command runs in its own Linux namespaces (bubblewrap) under systemd limits, sees only
its workspace and declared toolchains, and reaches the network only through an allowlisting proxy.
Provider credentials are stored under your user data directory, outside the repository and outside
every sandbox.

If you find a way out of the sandbox, a way for a worker to read what it should not (credentials,
another project, the engine), or a way to make the engine act on text it reads as if it were an
instruction, please report it privately through
[GitHub's security advisories](https://github.com/benteigland11/mizpah/security/advisories/new)
rather than a public issue. Include what you ran and what you saw.
