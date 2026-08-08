---
name: paperless-search
description: Use when searching, tagging, or organizing documents in Moritz's Paperless-ngx via the paperless-ngx MCP tools (paperless_documents_search, paperless_tags_*, paperless_correspondents_*, paperless_custom_fields_*).
---

# Paperless-ngx search & organization

- Prefer `paperless_documents_search` with `query` for full-text search; only
  fall back to listing tags/correspondents first if the user's phrasing is
  ambiguous (e.g. a company name that could be a correspondent or just
  content).
- Use `tags`/`tagsExclude`/`correspondent`/`documentType`/`storagePath` filters
  instead of over-broad full-text queries when the intent maps cleanly to a
  Paperless field.
- Leave `includeContent` off (default) for search/browse; use
  `paperless_documents_get` to pull full content for a specific doc once
  found — keeps result sizes small.
- When tagging or setting correspondents/document types, check
  `paperless_tags_list` / `paperless_correspondents_list` /
  `paperless_document_types_list` first and reuse existing entries rather
  than creating near-duplicates (e.g. "Amazon" vs "amazon.de").
- Use `paperless_documents_bulk_update` for multi-document tagging instead of
  looping single updates.
