import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { sveltekit } from "@sveltejs/kit/vite";

const host = process.env.TAURI_DEV_HOST;

// Absolute path to this checkout's loops/ registry, baked in at build time so the
// webview can hand Tauri commands ONE canonical location regardless of the app
// process's cwd (launcher runs from orrery/, cargo from orrery/src-tauri/).
// Override per-machine with VITE_LOOPS_DIR (env or orrery/.env). Forward slashes
// keep the string uniform across platforms (Windows APIs accept them).
const loopsDir = (
  process.env.VITE_LOOPS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "loops")
).replace(/\\/g, "/");

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [sveltekit()],

  define: {
    __ORRERY_LOOPS_DIR__: JSON.stringify(loopsDir),
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri` AND all loop runtime output. A running
      //    loop writes log.jsonl / run.out / checkpoint.json / sprint-status.yaml — and the
      //    Brake STOP flag — under loops/<id>/.loop, which sits inside the Vite root. Without
      //    these ignores, every log append (and the Brake click that writes STOP) trips an HMR
      //    full reload that wipes live UI state (e.g. the optimistic brake). The app reads
      //    loops/ via Tauri commands, never as modules, so Vite never needs to watch them.
      ignored: ["**/src-tauri/**", "**/loops/**", "**/.loop/**"],
    },
  },
}));
