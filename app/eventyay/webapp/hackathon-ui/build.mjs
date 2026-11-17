import { build } from 'esbuild'
import { copyFile, readFile, stat } from 'node:fs/promises'
import { gzipSync } from 'node:zlib'

const output = '../../static/hackathon'
await build({
  entryPoints: ['src/main.tsx'], bundle: true, minify: true,
  format: 'esm', target: ['chrome110', 'safari16'],
  define: {'process.env.NODE_ENV': '"production"'},
  outfile: `${output}/ui.js`, legalComments: 'eof',
  banner: {js: '/*! RUC Makers UI; shadcn/ui MIT Copyright (c) 2023 shadcn. See hackathon-ui-LICENSE.txt. */'},
})
await copyFile('src/ui-components.css', `${output}/ui-components.css`)
await copyFile('LICENSE.shadcn.md', `${output}/hackathon-ui-LICENSE.txt`)
const js = await readFile(`${output}/ui.js`)
console.log(`ui.js: ${(await stat(`${output}/ui.js`)).size} bytes; gzip ${gzipSync(js).length} bytes`)
