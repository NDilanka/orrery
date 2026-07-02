import { defineConfig } from "vite";
import { sveltekit } from "@sveltejs/kit/vite";

const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [sveltekit()],

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
