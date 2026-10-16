// Immutable compiler settings for the Vitest bundler profile; no project tsconfig.
import fs from 'node:fs';
import path from 'node:path';
import ts from './node_modules/typescript/lib/typescript.js';

const root = '/work/project';
const suffixes = new Set(['.js', '.mjs', '.cjs', '.ts', '.mts', '.cts']);
const files = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
    const full = path.join(directory, entry.name);
    // Dirent.isDirectory() does not follow the guest node_modules symlink.
    if (entry.isDirectory()) walk(full);
    else if (entry.isFile() && suffixes.has(path.extname(entry.name))) files.push(full);
  }
}
walk(root);
files.sort();
if (!files.length) {
  console.log('No JavaScript/TypeScript files found.');
  process.exit(1);
}
const options = {
  noEmit: true, strict: true, allowJs: true, checkJs: false,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
  allowImportingTsExtensions: true, resolveJsonModule: true,
  typeRoots: ['/opt/aegisops/node_modules/@types'], types: ['node'],
  skipLibCheck: false,
};
const program = ts.createProgram(files, options);
const diagnostics = ts.getPreEmitDiagnostics(program);
const host = {getCurrentDirectory: () => root, getCanonicalFileName: f => f, getNewLine: () => '\n'};
if (diagnostics.length) console.log(ts.formatDiagnostics(diagnostics, host));
else console.log(`Syntax and types checked: ${files.length} files. JavaScript checkJs=false; Bundler resolution.`);
process.exit(diagnostics.length ? 1 : 0);
