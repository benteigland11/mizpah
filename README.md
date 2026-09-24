# Mizpah

**A long-horizon agent harness for work nobody can check directly.**

Agents are good when there is a right answer to test against. Most real work has none: a design, a
piece of music, a deployment plan. Mizpah breaks that kind of work into small pieces that *can* be
checked, measures each one, and only calls the work done when the evidence says so.

You write a brief. Agents turn it into questions ("does the left hand ever stretch past an
octave?"), answer each with a measurement, and keep the methods and code that worked, so the next
task starts ahead of the last one.

<!-- screenshot: the desk, with the inbox, a brief and a running task -->

## Try it

Linux only for now. You need [uv](https://docs.astral.sh/uv/), the
[Flutter SDK](https://docs.flutter.dev/get-started/install/linux), and a few system packages
(Fedora shown):

```bash
sudo dnf install bubblewrap socat gtk3-devel mpv-devel libnotify-devel libayatana-appindicator-gtk3-devel clang cmake ninja-build
```

Then:

```bash
git clone https://github.com/benteigland11/mizpah.git
cd mizpah
uv sync --python 3.12 --managed-python
cd app && flutter run -d linux
```

Connect a model under **Providers** (a ChatGPT or Grok subscription, an API key, or a local
server), then tell the Deputy on **Home** what you want built. It drafts a brief; you sign it; the
work starts.

> Subscription sign-in for ChatGPT and Grok is unofficial and may stop working at any time. API
> keys and local servers are unaffected.

## More

- [Glossary](prompts/glossary.md) · [Contributing](CONTRIBUTING.md) · the story behind it: **mizpah.ai** (soon)
- Licensed under [Apache 2.0](LICENSE); see [NOTICE](NOTICE) for the parts under other licenses.
