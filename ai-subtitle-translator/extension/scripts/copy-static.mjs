import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, 'dist');

mkdirSync(dist, { recursive: true });

const filesToCopy = ['manifest.json', 'popup.html'];
for (const file of filesToCopy) {
  copyFileSync(join(root, file), join(dist, file));
}

console.log(`Copied ${filesToCopy.join(', ')} into dist/`);
