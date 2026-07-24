import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  // `dist` is build output. `e2e` is the Playwright suite: it runs under Node
  // in the pinned Playwright Docker image (not the browser), so it uses Node /
  // Playwright globals this browser-app config would flag. It has its own
  // toolchain; don't lint it with the app config.
  { ignores: ['dist', 'e2e'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'no-unused-vars': [
        'error',
        {
          varsIgnorePattern: '^[A-Z_]',
          // Destructured params in arrow callbacks are treated as args here.
          // Allow capitalized names so JSX-component aliases like
          // `({ icon: Icon })` don't trip the rule when used in JSX.
          argsIgnorePattern: '^[A-Z_]',
        },
      ],
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
]
