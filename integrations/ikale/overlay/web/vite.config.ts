import { execFileSync } from 'node:child_process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { detectorImage, docsBase, siteOrigin } from '../docs/site.config.ts'
import { zh } from './src/i18n/messages.ts'
import { apiProxy } from './scripts/vite-proxy.ts'
import telemetry from './telemetry.json' with { type: 'json' }

// The detector sits one level above the docs, and its preview image is rendered by the docs build.
const docsUrl = `${siteOrigin}${docsBase}/`
const pageUrl = new URL('..', docsUrl).href
const imageUrl = new URL(detectorImage, docsUrl).href
const meta = (key: 'name' | 'property', name: string, content: string) => ({ tag: 'meta', attrs: { [key]: name, content }, injectTo: 'head' as const })

// The footer shows the commit and build time. Hosted builds may lack `.git`, so fall back to the platform's commit variable.
let commit: string | null = null
try {
  commit = execFileSync('git', ['rev-parse', '--short=8', 'HEAD'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
} catch {
  commit = (process.env.VERCEL_GIT_COMMIT_SHA || process.env.CF_PAGES_COMMIT_SHA)?.slice(0, 8) ?? null
}
const build = { commit, time: new Date().toISOString() }

export default defineConfig({
  define: { __BUILD__: JSON.stringify(build) },
  root: fileURLToPath(new URL('.', import.meta.url)),
  envDir: fileURLToPath(new URL('..', import.meta.url)),
  base: './',
  plugins: [
    react(),
    tailwindcss(),
    apiProxy(),
    {
      name: 'telemetry',
      apply: 'build',
      transformIndexHtml: () => telemetry.scripts.map(script => ({
        tag: 'script',
        attrs: {
          defer: true,
          src: `${telemetry.origin}/${script}`,
          'data-website-id': telemetry.websiteId,
        },
        injectTo: 'head' as const,
      })),
    },
    {
      // index.html is Chinese, so link previews use the Chinese title and description.
      name: 'open-graph',
      transformIndexHtml: () => [
        meta('name', 'description', zh.app.description),
        meta('property', 'og:type', 'website'),
        meta('property', 'og:site_name', zh.app.name),
        meta('property', 'og:locale', 'zh_CN'),
        meta('property', 'og:title', `${zh.detect.title} · ${zh.app.name}`),
        meta('property', 'og:description', zh.app.description),
        meta('property', 'og:url', pageUrl),
        meta('property', 'og:image', imageUrl),
        meta('name', 'twitter:card', 'summary_large_image'),
        meta('name', 'twitter:image', imageUrl),
      ],
    },
  ],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    rolldownOptions: {
      output: {
        // Hex hashes moved every asset to a new URL on 2026-10-01, after browsers cached an HTML fallback under the old
        // entry name with a one-year immutable header.
        hashCharacters: 'hex',
        // 依赖按用途拆成独立分块，应用代码更新时浏览器仍可复用已缓存的依赖。
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|react-router|scheduler)[\\/]/, priority: 3 },
            { name: 'ui', test: /node_modules[\\/](@base-ui|@floating-ui|sonner|lucide-react)[\\/]/, priority: 2 },
            { name: 'motion', test: /node_modules[\\/](framer-motion|motion-dom|motion-utils)[\\/]/, priority: 1 },
          ],
        },
      },
    },
  },
})
