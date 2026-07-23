// Self-hosted fonts so the desktop Tauri app renders correctly offline.
// These @fontsource imports register @font-face declarations under the exact
// family names "DM Sans Variable" / "JetBrains Mono" that tokens.css references
// (--font-grotesk, --font-mono, --num), letting Vite bundle the .woff2 files
// instead of relying on the Google Fonts CDN.
//
// ORCHESTRATOR: import this module once from +page.svelte (`import '$lib/fonts';`)
// and remove the Google Fonts <link rel="preconnect"> + stylesheet from svelte:head.

// DM Sans (variable) — UI / headings (--font-grotesk). One variable import
// carries the full 400–700 weight axis the chrome uses. (The token name stays
// --font-grotesk to avoid renaming ~16 consumers; only the family changed.)
import '@fontsource-variable/dm-sans';

// JetBrains Mono — code / tabular numbers (--font-mono, --num)
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
