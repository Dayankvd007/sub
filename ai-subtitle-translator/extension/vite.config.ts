import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Rollup refuses iife/umd output for a multi-entry build (each entry would
// need its own global scope), so this uses the default ES-module output
// instead. None of the three entries import from one another, so Rollup
// inlines each entry's own imports (e.g. background.ts's imports of
// normalize.ts / mainWorldExtractor.ts) and emits three fully independent
// files with no import/export statements at all — safe to load as plain
// classic scripts, which MV3 content scripts require.
export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    rollupOptions: {
      input: {
        content: resolve(__dirname, 'src/content.ts'),
        background: resolve(__dirname, 'src/background.ts'),
        popup: resolve(__dirname, 'src/popup.ts'),
      },
      output: {
        entryFileNames: '[name].js',
      },
    },
  },
});
