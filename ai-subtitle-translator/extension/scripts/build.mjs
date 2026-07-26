/**
 * Builds each extension entry as its OWN single-entry Rollup build.
 *
 * This is not a stylistic choice. A multi-entry build extracts any module
 * shared between entries into a separate chunk and emits `import` statements
 * in the entry files — and an MV3 content script is a *classic* script, so an
 * import statement makes the whole content script fail to load with a
 * SyntaxError. Since Phase 3, `messages.ts` is shared by content.ts and
 * background.ts, so that split would happen.
 *
 * One build per entry, each with `format: 'iife'`, guarantees every entry is a
 * self-contained classic script with no import/export statements at all.
 */

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const ENTRIES = ['content', 'background', 'popup'];

for (const [index, name] of ENTRIES.entries()) {
  await build({
    root,
    configFile: false,
    logLevel: 'warn',
    build: {
      outDir: 'dist',
      // Only the first build may clear the directory.
      emptyOutDir: index === 0,
      target: 'es2022',
      rollupOptions: {
        input: resolve(root, `src/${name}.ts`),
        output: {
          format: 'iife',
          entryFileNames: `${name}.js`,
          inlineDynamicImports: true,
        },
      },
    },
  });
  console.log(`Built ${name}.js`);
}
