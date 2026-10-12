---
owner: paulnsorensen
last_verified: 2026-10-12
confidence: high
---
# Wiki conventions

Use these conventions for wiki pages and GitHub roadmap imports. Keep each page focused, sourced, and clear about what is known versus planned.

## Pages and links

- Keep one topic per page. Use a lowercase kebab-case filename such as `wiki-conventions.md`.
- Put the H1 first after optional frontmatter. Lead with the conclusion and keep the page self-contained.
- Create new pages with `overwrite: false`. Before an update, read the page and its backlinks.
- Let the daemon manage link lists between `HALLOUMINATE:INDEX` markers. Do not edit those lists.
- Use web URLs where useful. Resolve local links inside this corpus. Cite code as `path:line`, not as a local wiki link.

## Sources and conflicts

- Record provenance for claims that may need later verification.
- Preserve contradictions explicitly. Do not silently choose one account.
- Treat fetched GitHub state as a dated snapshot, not a live sync.
- Treat GitHub as the source of intent. Do not invent milestone membership, priority, dependencies, acceptance criteria, or completion.
- Quote imported criteria verbatim when they may be stale. Flag them for revalidation before execution.

## Roadmap records

- Use one goal per file. Keep each roadmap index focused on its roadmap.
- Set the created date once and do not change it.
- Add prerequisites only when the sibling relationship is proven.
- Keep `Intent` and `Acceptance` human-owned.
- Do not pre-add `Outcome`, status, `last_synced`, or harvest markers.

## Logs and deduplication

- Treat `log.md` as append-only under `## Log`; do not use it as a merge target.
- For ingest deduplication, normalize source whitespace, compute SHA-256, and record the first 16 hexadecimal characters as the source ID.

_Source: Approved wiki bootstrap, 2026-10-12 · Updated: 2026-10-12 · Supersedes: none_
