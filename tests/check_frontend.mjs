// Check every inline script without requiring a frontend build or dependencies.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
if (!scripts.length) throw new Error('No inline frontend scripts found');
scripts.forEach((match, index) => new vm.Script(match[1], { filename: `index.html script ${index + 1}` }));
console.log(`Checked ${scripts.length} frontend scripts.`);
