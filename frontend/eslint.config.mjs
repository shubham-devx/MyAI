import nextVitals from 'eslint-config-next/core-web-vitals'
import nextTypescript from 'eslint-config-next/typescript'
import globals from 'globals'

const config = [
  ...nextVitals,
  ...nextTypescript,
  {
    files: ['tests/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
  {
    ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'],
  },
]

export default config
