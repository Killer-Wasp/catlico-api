# Viewing the TheHive 5 docs offline

I could **not** mirror the StrangeBee docs from this environment: every page on
`docs.strangebee.com` (and its `sitemap.xml`) returns **HTTP 403 / Cloudflare** to
automated clients here, and the content is StrangeBee's copyrighted material — so
this repo does **not** contain copies of their pages. Instead you have:

1. **My own offline-readable summaries** (free to keep/share): the documentation
   [map](./thehive5-docs-map.md) and [deep digest](./thehive5-docs-deep.md).
2. **A URL manifest** of every page I found: [`thehive5-doc-links.txt`](./thehive5-doc-links.txt)
   (~90 URLs) — use it to make your **own** offline copy from a network that
   isn't blocked, for personal use and subject to StrangeBee's terms.

## Make an offline mirror on your own machine

The site is a normal browser to *you* — the 403 only happens to this sandbox.
Run one of these locally:

**`wget` from the URL list (flat copy of the listed pages):**
```bash
wget --input-file=thehive5-doc-links.txt \
     --adjust-extension --convert-links --page-requisites \
     --no-parent --wait=1 --random-wait \
     --user-agent="Mozilla/5.0" \
     --directory-prefix=thehive5-offline
# open thehive5-offline/docs.strangebee.com/.../index.html in a browser
```

**`wget --mirror` (recursive — also follows links to pages not in the list):**
```bash
wget --mirror --convert-links --adjust-extension --page-requisites \
     --no-parent --wait=1 --random-wait --user-agent="Mozilla/5.0" \
     https://docs.strangebee.com/thehive/overview/
```

**HTTrack (GUI/CLI website copier):**
```bash
httrack "https://docs.strangebee.com/thehive/" \
        -O ./thehive5-offline "+docs.strangebee.com/thehive/*" -%v
```

Notes:
- Be polite: keep `--wait`/rate limits so you don't hammer the site; respect
  `robots.txt` and StrangeBee's terms of use. This is for personal/offline
  reading, not redistribution.
- If Cloudflare also challenges your client, run the crawl from a logged-in
  browser session (e.g. the SingleFile / "Save Page WE" extension, or a browser
  automation profile) rather than headless `wget`.
- Some StrangeBee plans/docs also offer a downloadable/PDF export — check the docs
  site or ask StrangeBee support; that's the cleanest official offline copy.

## Keep the manifest fresh

`thehive5-doc-links.txt` is the set of pages discovered via search, not a
guaranteed-complete list. `wget --mirror` (second command) will discover the rest
by following links from the overview page.
