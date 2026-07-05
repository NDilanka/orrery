"""``orrery_loop.qa`` — acceptance-criteria-driven functional QA harness.

A generic companion to the dev loop: instead of *building* an app against frozen
acceptance criteria, it *exercises the running app* against them through a real
(headless) browser, judges each browser-observable criterion, and authors
deterministic E2E specs that become the durable regression gate.

The app under test is a *configuration* (base URL, stories glob, spec output dir,
auth storage-state), never baked into the engine — the same way the generic
fix-until-green loop treats its task. ``manifest`` turns a directory of BMAD-style
story ``.md`` files into the per-epic AC oracle the discovery pass judges against.
"""
