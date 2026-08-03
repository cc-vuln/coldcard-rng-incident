import type { APIRoute } from 'astro';

export const prerender = true;

const schema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  $id: 'https://cc-vuln.org/schemas/source-register-v1.json',
  title: 'cc-vuln.org source register v1',
  type: 'object',
  additionalProperties: false,
  required: [
    'schema', 'incident', 'archive_last_capture', 'interpretation',
    'web_sources', 'social_posts',
  ],
  properties: {
    schema: { const: 'https://cc-vuln.org/schemas/source-register-v1.json' },
    incident: { type: 'string' },
    archive_last_capture: { type: ['string', 'null'], format: 'date-time' },
    interpretation: { type: 'object', additionalProperties: { type: 'string' } },
    web_sources: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'url', 'capture', 'differences'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          url: { type: 'string', format: 'uri' },
          organisation: { type: ['string', 'null'] },
          kind: { type: ['string', 'null'] },
          role: { type: 'string' },
          publication_time: { type: ['string', 'null'] },
          note: { type: ['string', 'null'] },
          capture: { type: 'object' },
          differences: { type: 'array' },
        },
      },
    },
    social_posts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'title', 'url', 'author', 'capture'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          url: { type: 'string', format: 'uri' },
          author: { type: 'string' },
          organisation: { type: ['string', 'null'] },
          posted: { type: ['string', 'null'], format: 'date-time' },
          role: { type: 'string' },
          why_registered: { type: ['string', 'null'] },
          capture: { type: 'object' },
        },
      },
    },
  },
};

export const GET: APIRoute = () => new Response(
  JSON.stringify(schema, null, 2) + '\n',
  {
    headers: {
      'Content-Type': 'application/schema+json; charset=utf-8',
      'Cache-Control': 'public, max-age=86400',
    },
  },
);
