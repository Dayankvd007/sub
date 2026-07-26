import { defineConfig } from 'vite';

/**
 * This config exists for Vitest. The extension bundle is NOT built from here:
 * see scripts/build.mjs, which runs one single-entry Rollup build per entry.
 *
 * Why: a multi-entry build extracts modules shared between entries into a
 * separate chunk and emits `import` statements in the entry files. MV3 content
 * scripts are classic scripts, so an import statement stops the content script
 * loading at all. Per-entry IIFE builds keep each output self-contained.
 */
export default defineConfig({
  test: {
    // DOM-dependent suites opt in per file with `// @vitest-environment jsdom`,
    // so the pure-logic suites stay fast in plain Node.
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
