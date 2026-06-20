# Security Policy

## Project status

Orrery is **pre-1.0 / alpha** software. APIs, the wire protocol, and the engine
internals may change between releases.

## Threat model you should understand before running it

The loop engine **spawns coding-agent CLIs that execute code and shell commands**
against a working directory, and runs your project's own test/build commands as the
"gate." Treat it like a CI runner you operate locally:

- Only point it at repositories and tasks you trust.
- Run it under an account / container with the least privilege it needs.
- The tool allowlist and `--permission-mode` bound what the agent may do — review
  them before an unattended run.

## Supported versions

Only the latest tagged release receives fixes during the alpha period.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** — do not open a public issue
for anything exploitable.

- Report privately via a **[GitHub Security Advisory](https://github.com/NDilanka/orrery/security/advisories/new)**.
- Please include: affected component (engine / orrery app / LAN server), a
  reproduction, and the impact.
- Expected first response: within 7 days during alpha.

The LAN-reach feature (`orrery` web/mobile server) is opt-in and token-gated; if your
report concerns it, note whether control (not just observe) was reachable.
