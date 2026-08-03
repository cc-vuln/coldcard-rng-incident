// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  // Set to the real domain before deploying: used for the sitemap and canonicals.
  site: process.env.SITE_URL || 'https://example.invalid',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    sitemap(),
  ],
});
