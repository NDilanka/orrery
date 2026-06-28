# brain2 — functional regression gate

The Playwright functional specs in `e2e/functional/*.e2e.ts` encode brain2's verified
behavior (Epic 2 capture → classify → display → correct → atomic-split, and more as they are
authored). They MUST stay green.

When the gate is **red**, a spec has detected a **regression** in the app. Your job each
iteration:

1. Read the failing spec and the app code it exercises.
2. Fix the **application code** (`app/`, `components/`, `convex/`, `lib/`) so the behavior
   matches the spec again. **Do NOT edit the specs to make them pass** — the `e2e/**` files
   are locked; the spec is the frozen contract.
3. Re-run the gate (`npx playwright test e2e/functional`) until green.

## Operational prerequisites (read once)

The gate runs the REAL Playwright suite, so the owner-gated TEST env must be loaded into the
process before launching the loop:

- App + auth: `NEXT_PUBLIC_CONVEX_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`,
  `E2E_CLERK_USER_{USERNAME,PASSWORD,ID}` (a dedicated TEST Clerk user — NOT production).
- Seeding (optional): `CONVEX_TEST_DEPLOY_KEY` lets `e2e/global-setup.ts` load the deterministic
  fixture into the dedicated TEST Convex deployment. Absent → it logs a warning and skips the
  seed (capture-only specs still pass).

brain2's `.env.local` holds these; the Playwright runner is Node, so load them explicitly
(e.g. run under a dotenv loader, or export them) — Node does not auto-read `.env.local`.
`playwright.config.ts`'s `webServer` builds + serves the app (or reuses a running `:3000`).

These specs ALSO run as part of brain2's existing Playwright e2e suite (`testDir: "e2e"`,
`testMatch: *.e2e.ts`) in CI — this loop adds the harness/Orrery integration (live viz +
optional fix-until-green) on top. For pure pass/fail detection without any agent edits, run the
loop with `--dry-run` (baseline gate only).
