// Self-hosted fonts so the desktop Tauri app renders correctly offline.
// These @fontsource imports register @font-face declarations under the exact
// family names "Space Grotesk" / "JetBrains Mono" that tokens.css references
// (--font-grotesk, --font-mono, --num), letting Vite bundle the .woff2 files
// instead of relying on the Google Fonts CDN.
//
// ORCHESTRATOR: import this module once from +page.svelte (`import '$lib/fonts';`)
// and remove the Google Fonts <link rel="preconnect"> + stylesheet from svelte:head.

// Space Grotesk — UI / headings (--font-grotesk)
import '@fontsource/space-grotesk/400.css';
import '@fontsource/space-grotesk/500.css';
import '@fontsource/space-grotesk/600.css';
import '@fontsource/space-grotesk/700.css';

// JetBrains Mono — code / tabular numbers (--font-mono, --num)
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
