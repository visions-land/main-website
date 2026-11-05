#!/usr/bin/env python3
"""Build visions.land from content.toml.

Every word on the site lives in content.toml. This script pours that content
into the templates in templates/ and writes the static pages that GitHub Pages
serves: the English site at the repository root, and Italian and Chinese under
it/ and zh/.

    python3 build.py            build, and report what still needs translating
    python3 build.py --serve    build, then serve the site on localhost:8000
    python3 build.py --strict   exit non-zero if anything is missing or stale
    python3 build.py --bless    mark the current translations as up to date
    python3 build.py --bless it only mark Italian as up to date

Standard library only, on purpose: there is nothing to install, and the files
this produces are the website.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content.toml"
TEMPLATES = ROOT / "templates"
HASHES = ROOT / ".translation-hashes.json"
STATUS = ROOT / "TRANSLATIONS.md"

PAGES = ("index", "chapters", "collection")

# Values that are raw HTML fragments and must not be escaped. Everything else
# is plain text and gets escaped, so an ampersand in a title can never break
# the page.
RAW_SUFFIXES = (".body", ".intro", ".note")

# Open Graph wants language_TERRITORY. The English copy is British.
OG_LOCALES = {"en": "en_GB", "it": "it_IT", "zh": "zh_CN"}

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")

# Han, kana, CJK punctuation and fullwidth forms.
CJK = "\u2e80-\u9fff\u3000-\u303f\uf900-\ufaff\uff00-\uffef"
# Chinese sets its dash as a doubled em dash, which is not itself a Han
# character; allow a run of dashes on either side of the break. The lookahead
# still has to reach a Han character, so an em dash in English prose - where
# the newline really should become a space - never matches.
CJK_EDGE = CJK + "\u2013\u2014"
CJK_BREAK = re.compile(f"(?<=[{CJK_EDGE}])[ \t]*\n[ \t]*(?=[{CJK_EDGE}]*[{CJK}])")

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")

# Stands in for a block placeholder that resolved to nothing, so that tidy()
# can drop the whole line instead of leaving a gap.
DROP_LINE = "\x00"

BANNER = """<!--
  GENERATED FILE - do not edit.

  Every word on this page comes from content.toml. Edit that file, then run:

      python3 build.py

  Editing this file directly means losing the change on the next build.
-->
"""


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# content.toml
# ---------------------------------------------------------------------------


def is_langdict(value: object, langs: tuple[str, ...]) -> bool:
    """True for a {en = ..., it = ..., zh = ...} block of translatable copy."""
    return isinstance(value, dict) and bool(value) and all(k in langs for k in value)


def collect(node: object, langs: tuple[str, ...], path: str = "") -> dict[str, dict]:
    """Walk the content tree and return every translatable entry by dotted key."""
    found: dict[str, dict] = {}
    if is_langdict(node, langs):
        found[path] = node
    elif isinstance(node, dict):
        for key, child in node.items():
            found.update(collect(child, langs, f"{path}.{key}" if path else key))
    return found


def load() -> tuple[dict, tuple[str, ...], dict[str, dict]]:
    if not CONTENT.exists():
        raise BuildError(f"{CONTENT.name} is missing")
    data = tomllib.loads(CONTENT.read_text(encoding="utf-8"))

    site = data.get("site")
    if not isinstance(site, dict):
        raise BuildError("content.toml needs a [site] table")
    langs = tuple(site.get("languages") or ())
    if not langs or langs[0] != "en":
        raise BuildError("[site] languages must list the codes, starting with 'en'")

    # [site] holds configuration, and the chapter/collection arrays are handled
    # by their own renderers, so neither is walked for translatable strings.
    translatable: dict[str, dict] = {}
    for key, child in data.items():
        if key in ("site", "chapters", "works"):
            continue
        translatable.update(collect(child, langs, key))

    # ...except the hero's alt text, which does belong to the copy.
    if is_langdict(site.get("hero_alt"), langs):
        translatable["site.hero_alt"] = site["hero_alt"]

    # An entry with no English is not yet written. Leaving it out keeps it from
    # masquerading as copy; any template that actually needs it will fail by
    # name, which is the error we want.
    unwritten = sorted(k for k, v in translatable.items() if Resolver._empty(v.get("en")))
    for key in unwritten:
        del translatable[key]

    return data, langs, translatable


# ---------------------------------------------------------------------------
# resolving a string into one language
# ---------------------------------------------------------------------------


class Resolver:
    """Picks a language for each string, falling back to English, and keeps
    note of every fallback it had to make."""

    def __init__(self, translatable: dict[str, dict], blessed: dict[str, dict]):
        self.translatable = translatable
        self.blessed = blessed
        self.missing: dict[str, set[str]] = {}
        self.stale: dict[str, set[str]] = {}

    @staticmethod
    def _empty(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, list):
            return len(value) == 0
        return False

    def value(self, key: str, lang: str):
        entry = self.translatable[key]
        english = entry.get("en")
        if self._empty(english):
            raise BuildError(f"{key}: the English value is empty, so there is nothing to fall back to")

        if lang == "en":
            return english

        own = entry.get(lang)
        if self._empty(own):
            self.missing.setdefault(lang, set()).add(key)
            return english

        seen = self.blessed.get(lang, {}).get(key)
        if seen is not None and seen != fingerprint(english):
            self.stale.setdefault(lang, set()).add(key)
        return own

    def rendered(self, key: str, lang: str) -> str:
        """The value as HTML: raw for prose, escaped for everything else, and
        <li> items for a list."""
        value = self.value(key, lang)
        raw = key.endswith(RAW_SUFFIXES)
        if isinstance(value, list):
            return "\n".join(f"<li>{item if raw else esc(item)}</li>" for item in value)
        text = collapse_cjk(str(value).strip())
        return text if raw else esc(text)


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def esc(text: object) -> str:
    """Escape for both text and double-quoted attribute contexts. An
    apostrophe is left alone: every attribute this build emits is
    double-quoted, and &#x27; litters the generated source for no gain."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def collapse_cjk(text: str) -> str:
    """Drop a newline that falls between two CJK characters.

    HTML renders a source newline as a space, which is what Latin text wants
    and what Chinese text does not: a line wrapped mid-sentence in content.toml
    would show a visible gap between the two Han characters either side of the
    break. Removing it lets the Chinese copy be wrapped as freely as the rest.
    A newline next to Latin text is left alone, where the space is correct."""
    return CJK_BREAK.sub("", text)


def inline(markup: str) -> str:
    """Flatten an authored prose fragment onto one line. Notes in content.toml
    are wrapped for readability there; inline in the page, those newlines would
    land at the wrong indentation."""
    return " ".join(collapse_cjk(markup).split())


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def out_path(lang: str, page: str) -> str:
    """Where a page lives, relative to the site root."""
    prefix = "" if lang == "en" else f"{lang}/"
    return f"{prefix}{page}.html"


def canonical_url(domain: str, lang: str, page: str) -> str:
    prefix = "" if lang == "en" else f"{lang}/"
    tail = "" if page == "index" else f"{page}.html"
    return f"{domain.rstrip('/')}/{prefix}{tail}"


def depth(lang: str) -> int:
    return 0 if lang == "en" else 1


# ---------------------------------------------------------------------------
# generated blocks
# ---------------------------------------------------------------------------


def langbar_block(data: dict, langs: tuple[str, ...], lang: str, page: str) -> str:
    tags = data["site"]["langtags"]
    labels = data["site"]["langlabels"]
    up = "../" * depth(lang)
    rows = []
    for other in langs:
        label, tag = esc(labels[other]), esc(tags[other])
        if other == lang:
            rows.append(f'<span aria-current="page" lang="{tag}">{label}</span>')
        else:
            href = esc(up + out_path(other, page))
            rows.append(
                f'<a href="{href}" hreflang="{tag}" lang="{tag}">{label}</a>'
            )
    return "\n".join(rows)


def hreflang_block(data: dict, langs: tuple[str, ...], page: str) -> str:
    domain = data["site"]["domain"]
    tags = data["site"]["langtags"]
    rows = [
        f'<link rel="alternate" hreflang="{esc(tags[l])}" '
        f'href="{esc(canonical_url(domain, l, page))}" />'
        for l in langs
    ]
    rows.append(
        f'<link rel="alternate" hreflang="x-default" '
        f'href="{esc(canonical_url(domain, "en", page))}" />'
    )
    return "\n".join(rows)


def chapters_block(data: dict, res: Resolver, lang: str) -> str:
    name_fmt = res.value("label.chapter_name", lang)
    soon = res.rendered("page.chapters.soon", lang)
    rows = []
    for index, ch in enumerate(data.get("chapters", [])):
        numeral = esc(ch.get("numeral", ""))
        heading = esc(name_fmt.format(n=ch.get("numeral", "")))
        title = esc(ch.get("title", "")).strip()
        label = f"{heading} &mdash; {title}" if title else heading
        year = esc(ch.get("year", ""))
        note = inline(str(resolve_field(ch, "note", lang, res, f"chapters[{index}].note") or ""))
        url = str(ch.get("url", "")).strip()
        accent = str(ch.get("accent", "")).strip()
        if accent and not HEX_COLOUR.fullmatch(accent):
            raise BuildError(
                f"chapters[{index}].accent: {accent!r} is not a hex colour "
                f"such as #1e90ff"
            )
        # Handed to the stylesheet as a custom property, so the rule itself
        # stays generic and the colour stays in content.toml.
        tint = f' style="--chapter-accent: {esc(accent)}"' if accent else ""

        inner = [f'  <span class="chapter__head">']
        inner.append(f'    <span class="chapter__title">{label}</span>')
        inner.append(f'    <span class="chapter__meta">{year}</span>')
        if not url:
            inner.append(f'    <span class="badge">{soon}</span>')
        inner.append("  </span>")
        if note:
            inner.append(f'  <span class="chapter__note">{note}</span>')
        body = "\n".join(inner)

        if url:
            rows.append(
                f'<li>\n'
                f'  <a class="chapter" href="{esc(url)}"{tint}>\n'
                + "\n".join("  " + line for line in body.split("\n"))
                + f"\n  </a>\n</li>"
            )
        else:
            # Not online yet: a div, so there is no broken link and nothing
            # focusable. Mirrors how .btn[aria-disabled] behaves.
            rows.append(
                f'<li>\n'
                f'  <div class="chapter chapter--soon" aria-disabled="true"{tint}>\n'
                + "\n".join("  " + line for line in body.split("\n"))
                + f"\n  </div>\n</li>"
            )
    return "\n".join(rows)


def resolve_field(item: dict, field: str, lang: str, res: Resolver, label: str):
    """Resolve a per-item {en=..., it=..., zh=...} field inside a [[chapters]]
    or [[works]] block, recording a fallback the same way Resolver does."""
    entry = item.get(field)
    if entry is None:
        return ""
    if not isinstance(entry, dict):
        return entry
    english = entry.get("en", "")
    if lang == "en":
        return english
    own = entry.get(lang)
    if own is None or (isinstance(own, str) and own.strip() == ""):
        if isinstance(english, str) and english.strip():
            res.missing.setdefault(lang, set()).add(label)
        return english
    seen = res.blessed.get(lang, {}).get(label)
    if seen is not None and seen != fingerprint(english):
        res.stale.setdefault(lang, set()).add(label)
    return own


def works_block(data: dict, res: Resolver, lang: str) -> str:
    labels = {
        key: res.rendered(f"label.{key}", lang)
        for key in ("artist", "chapter", "acquired", "held")
    }
    rows = []
    for index, work in enumerate(data.get("works", [])):
        meta = []

        artist = str(work.get("artist", "")).strip()
        if artist:
            url = str(work.get("artist_url", "")).strip()
            value = (
                f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer"'
                f">{esc(artist)}</a>"
                if url
                else esc(artist)
            )
            meta.append((labels["artist"], value))

        chapter = str(work.get("chapter", "")).strip()
        if chapter:
            meta.append((labels["chapter"], esc(chapter)))

        acquired = str(work.get("acquired", "")).strip()
        if acquired:
            meta.append(
                (labels["acquired"], f'<time datetime="{esc(acquired)}">{esc(acquired)}</time>')
            )

        held = str(resolve_field(work, "held", lang, res, f"works[{index}].held") or "").strip()
        if held:
            meta.append((labels["held"], esc(held)))

        note = inline(str(resolve_field(work, "note", lang, res, f"works[{index}].note") or ""))

        lines = ["<li>", '  <article class="work">']
        lines.append(f'    <h2 class="work__title">{esc(work.get("title", ""))}</h2>')
        if meta:
            lines.append('    <dl class="work__meta">')
            for label, value in meta:
                lines.append('      <div class="work__row">')
                lines.append(f"        <dt>{label}</dt>")
                lines.append(f"        <dd>{value}</dd>")
                lines.append("      </div>")
            lines.append("    </dl>")
        if note:
            lines.append(f'    <p class="work__note">{note}</p>')
        lines.append("  </article>")
        lines.append("</li>")
        rows.append("\n".join(lines))
    return "\n".join(rows)


def hero_block(data: dict, res: Resolver, lang: str) -> str:
    hero = str(data["site"].get("hero", "")).strip()
    if not hero:
        return ""
    alt = res.rendered("site.hero_alt", lang) if "site.hero_alt" in res.translatable else ""
    up = "../" * depth(lang)
    return (
        '<div class="card-hero">\n'
        f'  <img src="{esc(up)}assets/{esc(hero)}" alt="{alt}" />\n'
        "</div>"
    )


def social_image_block(data: dict, lang: str) -> str:
    hero = str(data["site"].get("hero", "")).strip()
    if not hero:
        return ""
    url = f"{data['site']['domain'].rstrip('/')}/assets/{hero}"
    return (
        f'<meta property="og:image" content="{esc(url)}" />\n'
        f'<meta name="twitter:image" content="{esc(url)}" />'
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render(template: str, values: dict[str, str], where: str) -> str:
    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise BuildError(
                f"{where}: the template uses {{{{ {key} }}}}, "
                f"which content.toml does not define"
            )
        value = values[key]
        # If the placeholder sits alone on its line, indent every line of the
        # value to match, so the generated HTML stays readable.
        line_start = template.rfind("\n", 0, match.start()) + 1
        prefix = template[line_start : match.start()]
        if prefix.strip() == "" and value == "":
            return DROP_LINE
        if prefix.strip() == "" and "\n" in value:
            first, *rest = value.split("\n")
            return "\n".join([first] + [prefix + line if line.strip() else line for line in rest])
        return value

    return PLACEHOLDER.sub(substitute, template)


def tidy(markup: str) -> str:
    """Strip trailing whitespace and collapse runs of blank lines, so that an
    empty placeholder does not leave a hole in the output."""
    out: list[str] = []
    for line in markup.split("\n"):
        line = line.rstrip()
        if line.strip() == DROP_LINE:
            continue
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def values_for(data: dict, res: Resolver, langs: tuple[str, ...], lang: str, page: str) -> dict[str, str]:
    site = data["site"]
    values: dict[str, str] = {}

    # Every translatable string, under its own dotted key.
    for key in res.translatable:
        values[key] = res.rendered(key, lang)

    # The current page's strings, also under a page-agnostic alias so that
    # base.html does not need to know which page it is rendering.
    for key, value in list(values.items()):
        head = f"page.{page}."
        if key.startswith(head):
            values["page." + key[len(head) :]] = value

    # [site] configuration.
    for key, value in site.items():
        if not isinstance(value, (dict, list)):
            values[f"site.{key}"] = esc(value)

    values.update(
        {
            "lang": lang,
            "langtag": esc(site["langtags"][lang]),
            "oglocale": OG_LOCALES.get(lang, "en_GB"),
            "rel": "../" * depth(lang),
            "canonical": esc(canonical_url(site["domain"], lang, page)),
            "langbar": langbar_block(data, langs, lang, page),
            "hreflang": hreflang_block(data, langs, page),
            "social_image": social_image_block(data, lang),
            "twitter_card": "summary_large_image" if str(site.get("hero", "")).strip() else "summary",
            "hero": hero_block(data, res, lang) if page == "index" else "",
            "block.chapters": chapters_block(data, res, lang) if page == "chapters" else "",
            "block.works": works_block(data, res, lang) if page == "collection" else "",
            "footer_back": ""
            if page == "index"
            else f'<p><a href="index.html">&larr; {res.rendered("btn.home", lang)}</a></p>',
        }
    )
    return values


def sitemap(data: dict, langs: tuple[str, ...]) -> str:
    domain = data["site"]["domain"]
    tags = data["site"]["langtags"]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for page in PAGES:
        for lang in langs:
            lines.append("  <url>")
            lines.append(f"    <loc>{esc(canonical_url(domain, lang, page))}</loc>")
            for other in langs:
                lines.append(
                    f'    <xhtml:link rel="alternate" hreflang="{esc(tags[other])}" '
                    f'href="{esc(canonical_url(domain, other, page))}" />'
                )
            lines.append(
                f'    <xhtml:link rel="alternate" hreflang="x-default" '
                f'href="{esc(canonical_url(domain, "en", page))}" />'
            )
            lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def report(res: Resolver, langs: tuple[str, ...], total: int) -> str:
    lines = ["# Translation status", ""]
    lines.append("Generated by `python3 build.py`. Do not edit by hand.")
    lines.append("")
    lines.append(
        "Anything listed as **missing** shows the English text on the site, so "
        "nothing is ever blank. Anything **stale** has a translation, but the "
        "English it was written from has since changed."
    )
    lines.append("")
    for lang in langs:
        if lang == "en":
            continue
        missing = sorted(res.missing.get(lang, ()))
        stale = sorted(res.stale.get(lang, ()))
        done = total - len(missing)
        lines.append(f"## {lang}")
        lines.append("")
        lines.append(f"- translated: {done}/{total}")
        lines.append(f"- missing: {len(missing)}")
        lines.append(f"- stale: {len(stale)}")
        lines.append("")
        if missing:
            lines.append("### Missing (showing English)")
            lines.append("")
            lines.extend(f"- `{key}`" for key in missing)
            lines.append("")
        if stale:
            lines.append("### Stale (English changed since this was written)")
            lines.append("")
            lines.extend(f"- `{key}`" for key in stale)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def console_report(res: Resolver, langs: tuple[str, ...], total: int) -> bool:
    clean = True
    print()
    print("  TRANSLATION STATUS")
    for lang in langs:
        if lang == "en":
            continue
        missing = sorted(res.missing.get(lang, ()))
        stale = sorted(res.stale.get(lang, ()))
        done = total - len(missing)
        note = []
        if missing:
            note.append(f"{len(missing)} missing")
        if stale:
            note.append(f"{len(stale)} stale")
        suffix = ("    " + ", ".join(note)) if note else "    complete"
        print(f"    {lang:<4} {done:>3}/{total} translated{suffix}")
        if missing or stale:
            clean = False
    for lang in langs:
        if lang == "en":
            continue
        missing = sorted(res.missing.get(lang, ()))
        stale = sorted(res.stale.get(lang, ()))
        if missing:
            print(f"  MISSING {lang}: {', '.join(missing)}")
        if stale:
            print(f"  STALE   {lang}: {', '.join(stale)}")
    print()
    if clean:
        print("  every string is translated and current.")
    else:
        print(f"  the site is publishable; untranslated strings show English. See {STATUS.name}.")
    return clean


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build(bless: list[str] | None) -> bool:
    data, langs, translatable = load()
    blessed = json.loads(HASHES.read_text(encoding="utf-8")) if HASHES.exists() else {}
    res = Resolver(translatable, blessed)

    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    written: list[str] = []
    for lang in langs:
        for page in PAGES:
            template = TEMPLATES / f"{page}.html"
            if not template.exists():
                raise BuildError(f"templates/{page}.html is missing")
            values = values_for(data, res, langs, lang, page)
            values["main"] = render(
                template.read_text(encoding="utf-8"), values, f"templates/{page}.html"
            ).strip()
            markup = tidy(render(base, values, "templates/base.html"))
            markup = markup.replace("<!DOCTYPE html>\n", "<!DOCTYPE html>\n" + BANNER, 1)

            target = ROOT / out_path(lang, page)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markup, encoding="utf-8")
            written.append(out_path(lang, page))

    (ROOT / "sitemap.xml").write_text(sitemap(data, langs), encoding="utf-8")
    written.append("sitemap.xml")

    total = len(translatable) + sum(
        1
        for group in ("chapters", "works")
        for item in data.get(group, [])
        for field in ("note", "held")
        if isinstance(item.get(field), dict) and str(item[field].get("en", "")).strip()
    )

    print("  wrote " + "  ".join(written))
    STATUS.write_text(report(res, langs, total), encoding="utf-8")
    clean = console_report(res, langs, total)

    if bless is not None:
        targets = bless or [l for l in langs if l != "en"]
        for lang in targets:
            if lang not in langs or lang == "en":
                raise BuildError(f"--bless: {lang!r} is not one of {langs[1:]}")
            marks = blessed.setdefault(lang, {})
            live = set(translatable)
            for key, entry in translatable.items():
                marks[key] = fingerprint(entry.get("en"))
            for group in ("chapters", "works"):
                for index, item in enumerate(data.get(group, [])):
                    for field in ("note", "held"):
                        if isinstance(item.get(field), dict):
                            key = f"{group}[{index}].{field}"
                            live.add(key)
                            marks[key] = fingerprint(item[field].get("en", ""))
            # Forget strings that no longer exist, so the record does not
            # accumulate keys for copy that has since been deleted.
            for gone in set(marks) - live:
                del marks[gone]
        HASHES.write_text(
            json.dumps(blessed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  blessed: {', '.join(targets)} marked as up to date with the English.")

    return clean


def main() -> int:
    parser = argparse.ArgumentParser(description="Build visions.land from content.toml.")
    parser.add_argument("--serve", action="store_true", help="serve the site after building")
    parser.add_argument("--port", type=int, default=8000, help="port for --serve (default 8000)")
    parser.add_argument(
        "--strict", action="store_true", help="exit non-zero if a translation is missing or stale"
    )
    parser.add_argument(
        "--bless",
        nargs="*",
        metavar="LANG",
        help="record the current English as reviewed for these languages (default: all)",
    )
    args = parser.parse_args()

    try:
        clean = build(args.bless)
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 2
    except KeyError as error:
        print(f"build failed: content.toml is missing {error}", file=sys.stderr)
        return 2

    if args.serve:
        import http.server
        import socketserver

        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", args.port), handler) as server:
            print(f"\n  serving {ROOT} at http://localhost:{args.port}/  (ctrl-c to stop)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print()

    return 1 if args.strict and not clean else 0


if __name__ == "__main__":
    sys.exit(main())
