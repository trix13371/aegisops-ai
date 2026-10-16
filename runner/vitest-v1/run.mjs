// Trusted Vitest 4.0.18 API wrapper. No user config, npm scripts or tool plugins.
import { startVitest } from 'vitest/node';

try {
  await startVitest('test', [], {
    root: '/work/project',
    // startVitest checks `config`, while Vite itself checks `configFile` below.
    config: false,
    run: true,
    watch: false,
    include: [
      '**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts}',
      '**/{test,spec}.{js,mjs,cjs,ts,mts,cts}',
    ],
    exclude: ['**/node_modules/**', '**/*.d.ts', '**/*.d.mts', '**/*.d.cts'],
    environment: 'node',
    globals: false,
    pool: 'forks',
    fileParallelism: false,
    maxWorkers: 1,
    maxConcurrency: 1,
    isolate: true,
    execArgv: ['--max-old-space-size=192'],
    allowOnly: false,
    passWithNoTests: false,
    setupFiles: [],
    globalSetup: [],
    coverage: {enabled: false},
    browser: {enabled: false},
    typecheck: {enabled: false},
    testTimeout: 5000,
    hookTimeout: 5000,
    teardownTimeout: 1000,
    retry: 0,
    update: false,
    cache: false,
    experimental: {fsModuleCache: false},
    reporters: ['default'],
    color: false,
    clearScreen: false,
    ui: false,
    open: false,
    api: false,
  }, {
    configFile: false,
    envDir: false,
    publicDir: false,
    cacheDir: '/tmp/aegisops-vite',
    css: {postcss: {plugins: []}},
    esbuild: {
      tsconfigRaw: {
        compilerOptions: {target: 'ES2022', useDefineForClassFields: true},
      },
    },
    optimizeDeps: {noDiscovery: true, include: []},
    server: {watch: null, hmr: false},
    clearScreen: false,
  }, {
    // Refuse optional package installation even inside the offline guest.
    packageInstaller: {
      async ensureInstalled() {
        throw new Error('Additional test environment packages are not supported.');
      },
    },
  });
} catch {
  console.error('Vitest could not initialize or complete the fixed Node profile.');
  process.exitCode = 1;
}
