# visions.land

The hub site for **Visions**: who we are, the chapter archive, and the
collection. Plain HTML and CSS, no framework, no runtime dependencies — the
files in this repository are the website.

Each chapter lives on its own subdomain, named for its year, with its own
repository: [2025.visions.land](https://2025.visions.land) (Foresta di
Cristallo), [2026.visions.land](https://2026.visions.land) (I popoli del mare)
and [2027.visions.land](https://2027.visions.land) (Roko's Basilisk).

> **Pending:** the chapter years were corrected upwards by one, so all three
> subdomains need re-pointing before the links here are right. Until that is
> done, `2025.visions.land` still serves I popoli del mare, `2026.visions.land`
> still serves Roko's Basilisk, and `2027.visions.land` does not exist.

## How this repository works

Every word on the site — in English, Italian and Chinese — lives in one file,
`content.toml`. A small script pours it into the templates and writes the
static pages that GitHub Pages serves.

**You edit `content.toml`. You do not edit the HTML.**

```sh
python3 build.py            # rebuild the site, and report what needs translating
python3 build.py --serve    # rebuild, then serve it at http://localhost:8000
```

Python 3.11 or newer, and nothing else — no `pip install`, no `node_modules`.

| File | What it is |
| --- | --- |
| `content.toml` | **All site copy, all three languages.** The single source of truth. |
| `build.py` | Renders the templates from `content.toml`. Run it before pushing. |
| `templates/` | The page shells. Structure only, no copy. Rarely needs touching. |
| `styles.css` | The whole design system; every colour/font/radius token is in the `:root` block. Hand-written, not generated. |
| `assets/` | The Visions wordmark, and any hero image. |
| `TRANSLATIONS.md` | Written by each build: what is still untranslated, and what has gone stale. |
| `index.html`, `chapters.html`, `collection.html` | **Generated.** English. |
| `it/`, `zh/` | **Generated.** The Italian and Chinese versions of the same three pages. |
| `sitemap.xml` | Generated. |
| `CNAME`, `robots.txt`, `.nojekyll` | GitHub Pages plumbing. |

Generated files carry a `GENERATED FILE — do not edit` banner at the top. They
are committed, because GitHub Pages serves them directly; editing one by hand
means losing the change on the next build.

## Editing copy

Open `content.toml`, find the string, change it, run `python3 build.py`, and
commit both `content.toml` and the regenerated HTML.

Prose values are raw HTML fragments, so `<p>`, `<em>`, `<strong>` and `<a>` all
work as you would expect. Keep each paragraph in its own `<p>`. Short values —
titles, labels, button text — are plain text and should not contain HTML.

## Translating

Each string has an `en`, an `it` and a `zh`:

```toml
[contact.heading]
en = "Contact"
it = "Contatti"
zh = "联系我们"
```

English is the source and the fallback. **Leave `it` or `zh` empty and the site
still builds**, showing the English in its place, and the build tells you which
strings are outstanding:

```
  TRANSLATION STATUS
    it    33/35 translated    2 missing
    zh    29/35 translated    5 missing, 1 stale
  MISSING it: about.body, works[0].note
  STALE   zh: page.index.title
```

- **missing** — no translation yet; the page shows English.
- **stale** — there *is* a translation, but the English it was written from has
  changed since. Worth re-reading.

The same report is written to `TRANSLATIONS.md` on every build, so you never
have to open nine HTML files to find out what is outstanding.

Staleness is tracked by fingerprinting each English string. After a round of
translating, record that the translations match the current English:

```sh
python3 build.py --bless        # all languages
python3 build.py --bless it     # just Italian
```

`python3 build.py --strict` exits non-zero if anything is missing or stale,
which is useful in a pre-push hook if you ever want one.

## Adding a chapter

Append a `[[chapters]]` block to `content.toml`, newest first. Leave `url`
empty and the entry renders greyed out with a "coming soon" badge instead of a
dead link, which is what to do for a chapter announced before its site exists.

`accent` is an optional hex colour taken from that chapter's own site; it
becomes the entry's border, so the archive reads as a row of coloured spines
on an otherwise hue-free page. Omit it for a neutral border. The build rejects
anything that is not a hex colour, so a typo fails loudly rather than
silently producing an unstyled border.

## Adding a work to the collection

Copy a whole `[[works]]` block in `content.toml`, paste it above the others so
the newest acquisition is first, and fill in the fields. `artist_url` and
`note` may be left empty. Dates are written `YYYY-MM` or `YYYY-MM-DD`.

The field labels — Artist, Acquired, Currently held at — come from the
`[label.*]` section and already exist in all three languages. You never
translate those per work.

`held` is the field most likely to drift out of date, since works travel. When
you have checked that every one of them is still true, bump
`provenance_reviewed` in `[site]`; the collection page shows it.

## Deploying to GitHub Pages

1. Build, then commit everything — sources *and* generated pages:

   ```sh
   python3 build.py
   git add -A && git commit -m "..."
   git remote add origin git@github.com:<owner>/<repo>.git
   git push -u origin main
   ```

2. **Settings → Pages**, set **Source** to *Deploy from a branch*, branch
   `main`, folder `/ (root)`.

3. **Settings → Pages → Custom domain**: `visions.land`. Wait for the
   certificate, then tick **Enforce HTTPS**.

All internal links are relative, so the site works at a domain root and at a
repository subpath (`/<repo>/`) without changes.

### DNS — different from the chapter sites

The chapter sites are *subdomains*, so each is a single `CNAME` record. An apex
domain cannot be a CNAME, so `visions.land` needs address records instead:

| Type | Name | Value |
| --- | --- | --- |
| `A` | `@` | `185.199.108.153` |
| `A` | `@` | `185.199.109.153` |
| `A` | `@` | `185.199.110.153` |
| `A` | `@` | `185.199.111.153` |
| `AAAA` | `@` | `2606:50c0:8000::153` |
| `AAAA` | `@` | `2606:50c0:8001::153` |
| `AAAA` | `@` | `2606:50c0:8002::153` |
| `AAAA` | `@` | `2606:50c0:8003::153` |
| `CNAME` | `www` | `<owner>.github.io` |

If the registrar supports `ALIAS` or `ANAME` at the apex, one of those pointing
at `<owner>.github.io` works too. Only one repository may claim `visions.land`
in its `CNAME` file.

## Notes

- **No JavaScript.** Because each language is a real page rather than a runtime
  switch, there is nothing to script: the language bar is three links, and every
  page is complete with JavaScript disabled.
- **Three languages, real URLs.** English at the root, Italian under `/it/`,
  Chinese under `/zh/`, with `hreflang` and `canonical` tags that match. The
  Chinese pages are marked `lang="zh-Hans"`, which `styles.css` uses to switch
  to a Simplified-Chinese font stack and to turn off the Latin hyphenation and
  letter-spacing that damage Han glyphs.
- **The palette** is deliberately hue-free. Each chapter site owns a hue of its
  own; the hub owns none, so it still reads as the institution when a fourth
  chapter arrives in a colour nobody has chosen yet. Everything is tokenised in the
  `:root` block of `styles.css`.
- **No hero image** on the front page by default: the wordmark carries the
  identity. To add one, put a 1280×635 JPEG in `assets/` (that is the size both
  chapter sites use), name it in `[site] hero`, fill in `hero_alt`, and rebuild.
  Download rather than hotlink, so the site keeps having no external runtime
  dependencies, and record the licence here.

## Outstanding

- `[about.body]` is **placeholder copy**, written in the intended register. It
  needs replacing with the real description.
- The acquisition date for *THEY* is **inferred**, not confirmed — see the
  `TODO` in `content.toml`. The Chapter II regulation transfers ownership on
  acceptance, which is not the date of the presentation. Where the work is held
  between now and December 2026 also needs filling in.
- Chapter I's one-line description in `content.toml` is inferred from that
  chapter's old site and has not been confirmed. The title and the link have.
- The Chinese copy has not been reviewed by a native speaker.

## Contact

<info@visions.land> — general enquiries
<partnerships@visions.land> — collaborations
<acquisitions@visions.land> — acquisitions, loans and borrowings
