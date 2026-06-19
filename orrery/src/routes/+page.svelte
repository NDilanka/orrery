<script lang="ts">
  // Orrery — compose the instrument. A loop-picker (bmad / roman / calc) loads
  // the matching fixture via the replay transport (or Tauri watch_run in the
  // real app), renders the Observatory full-bleed with the HUD + control bar
  // overlaid. Obsidian, instrument feel — not a default-light dashboard.

  import '$lib/render/tokens.css';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { base } from '$app/paths';
  import Observatory from '$lib/render/Observatory.svelte';
  import Mechanism from '$lib/render/Mechanism.svelte';
  import CostQuotaStrip from '$lib/render/CostQuotaStrip.svelte';
  import Hud from '$lib/panels/Hud.svelte';
  import RunControlBar from '$lib/panels/RunControlBar.svelte';
  import VerdictPanel from '$lib/panels/VerdictPanel.svelte';
  import TransportBar from '$lib/panels/TransportBar.svelte';
  import { runStore } from '$lib/stores/run.svelte';
  import {
    createTransport,
    hasTauri,
    LOOPS,
    type LoopChoice,
    type Transport,
  } from '$lib/transport';
  import { isPlayback, type PlaybackState, type PlaybackTransport } from '$lib/transport/replay';

  let selected = $state<string>('demo');
  let transport: Transport | null = null;
  let mode = $state<'live' | 'replay'>('replay');

  // playback (dev replay only) — drives the scrub/play/pause/speed control
  let playback = $state<PlaybackTransport | null>(null);
  let playbackState = $state<PlaybackState>({
    playing: false,
    speed: 1,
    cursor: 0,
    total: 0,
    done: false,
  });

  // resolve fixture URLs against SvelteKit's base path so it works under any route
  function withBase(choice: LoopChoice): LoopChoice {
    return {
      ...choice,
      fixtureUrl: `${base}/${choice.fixtureUrl}`,
      checkpointUrl: choice.checkpointUrl ? `${base}/${choice.checkpointUrl}` : undefined,
    };
  }

  async function load(id: string) {
    if (!browser) return;
    transport?.stop();
    runStore.reset();
    playback = null;
    const choice = LOOPS.find((l) => l.id === id);
    if (!choice) return;
    transport = createTransport(withBase(choice), {
      onState: (s) => runStore.set(s),
    });
    if (isPlayback(transport)) {
      playback = transport;
      transport.onPlayback((p) => (playbackState = p));
    }
    await transport.start();
  }

  async function control(action: string) {
    await transport?.control(action);
  }

  function pick(id: string) {
    selected = id;
    void load(id);
  }

  onMount(() => {
    mode = hasTauri() ? 'live' : 'replay';
    void load(selected);
    return () => transport?.stop();
  });
</script>

<svelte:head>
  <title>Orrery — {selected}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link
    href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
    rel="stylesheet"
  />
</svelte:head>

<main class="stage">
  {#if browser}
    <Observatory />
    <Mechanism />
    <CostQuotaStrip />
    <Hud />
    <VerdictPanel />
    {#if playback}
      <TransportBar transport={playback} state={playbackState} />
    {/if}
    <RunControlBar {control} />
  {/if}

  <!-- loop picker -->
  <nav class="picker">
    <div class="brand mono">✦ ORRERY <span class="mode">{mode}</span></div>
    <div class="loops">
      {#each LOOPS as loop (loop.id)}
        <button
          class="loop {selected === loop.id ? 'active' : ''}"
          onclick={() => pick(loop.id)}
        >
          <span class="lid mono">{loop.id}</span>
          <span class="lname">{loop.name}</span>
        </button>
      {/each}
    </div>
  </nav>
</main>

<style>
  .stage {
    position: fixed;
    inset: 0;
    overflow: hidden;
    background: var(--void);
  }
  .picker {
    position: absolute;
    top: 18px;
    right: 18px;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 10px;
    padding: 14px 16px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    z-index: 10;
  }
  .brand {
    font-size: 13px;
    letter-spacing: 0.22em;
    color: var(--brass);
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .mode {
    font-size: 9px;
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    background: var(--void-3);
    color: var(--text-dim);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .loops {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 220px;
  }
  .loop {
    display: flex;
    flex-direction: column;
    gap: 2px;
    text-align: left;
    padding: 9px 12px;
    border-radius: 8px;
    border: 1px solid var(--hairline);
    background: transparent;
    color: var(--starlight);
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .loop:hover { border-color: color-mix(in srgb, var(--brass) 40%, transparent); }
  .loop.active {
    border-color: var(--brass);
    background: color-mix(in srgb, var(--brass) 9%, transparent);
  }
  .lid {
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--brass);
  }
  .lname {
    font-size: 12px;
    color: var(--text-dim);
  }
</style>
