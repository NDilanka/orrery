# What / why

<!-- What changed and why. Keep the PR scoped. -->

## How tested

<!-- Delete lines that don't apply. -->

- [ ] Engine: `pytest engine/tests` (and `ruff check engine`)
- [ ] App: `npm run check` (in `orrery/`)
- [ ] App: `npm run test:unit` (in `orrery/`)
- [ ] Rust: `cargo test --manifest-path src-tauri/Cargo.toml` (in `orrery/`)
- [ ] e2e (if UI behavior changed)

## Checklist

- [ ] Tests pass locally
- [ ] If event shapes or `RunState` changed: updated `orrery/PROTOCOL.md`, the engine emitter, `reducer.rs`, and `reduce.ts` in this PR
- [ ] Docs updated where behavior changed
- [ ] No secrets, tokens, or personal paths in the diff
