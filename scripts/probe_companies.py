"""Find which ATS a company uses and its board slug.

Usage:
    python scripts/probe_companies.py "Acme Robotics" "Example AI"
    python scripts/probe_companies.py --slug acme "Acme Robotics"   # also try an explicit slug

For each company, tries a few slug spellings (acmerobotics, acme-robotics, acme...) on the
public Greenhouse, Lever and Ashby feeds and prints every board that exists, with its open-role
count, as a ready-to-paste companies.yaml line. Comeet needs a uid and token from the careers
page HTML, so it can't be probed by name.

A hit is a board that exists, which is not proof it belongs to the right company: check the
role count and, when in doubt, the careers page.
"""

import argparse
import re
import sys

import httpx

FEEDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

_SUFFIXES = r"\b(inc|ltd|llc|corp|corporation|technologies|technology|labs|group|co)\b\.?"


def slug_variants(name: str) -> tuple[list[str], list[str]]:
    """Plausible board slugs for a company name: (likely, first-word fallbacks)."""
    base = name.lower().strip()
    short = re.sub(_SUFFIXES, "", base).strip(" ,.")
    likely: list[str] = []
    fallback: list[str] = []
    for n in (base, short):
        words = re.findall(r"[a-z0-9]+", n)
        if not words:
            continue
        likely += ["".join(words), "-".join(words)]
        if "." in n:  # "monday.com", "gong.io"
            likely.append(n.replace(" ", ""))
        if len(words) > 1:
            fallback.append(words[0])
    likely = list(dict.fromkeys(likely))
    return likely, [v for v in dict.fromkeys(fallback) if v not in likely]


def probe(source: str, slug: str, client: httpx.Client) -> int | None:
    """Open-role count when the board exists, else None."""
    try:
        resp = client.get(FEEDS[source].format(slug=slug))
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    if source == "greenhouse":
        return len(data.get("jobs", []))
    if source == "lever":
        return len(data) if isinstance(data, list) else None  # {"ok": false} = not found
    return len(data.get("jobs", []))


def _probe_all(slugs: list[str], client: httpx.Client) -> list[tuple[str, str, int]]:
    return [
        (source, slug, count)
        for slug in slugs
        for source in FEEDS
        if (count := probe(source, slug, client)) is not None
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("companies", nargs="+", help="Company names")
    parser.add_argument("--slug", action="append", default=[], help="Extra slug to try for every company")
    args = parser.parse_args()

    found_any = False
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for name in args.companies:
            likely, fallback = slug_variants(name)
            note = ""
            hits = _probe_all(list(dict.fromkeys(args.slug + likely)), client)
            if not hits and fallback:
                hits = _probe_all(fallback, client)
                note = ", first-word guess: verify"
            if not hits:
                print(f"# {name}: not found on Greenhouse, Lever or Ashby")
                continue
            found_any = True
            for source, slug, count in hits:
                print(f"{source:10}  - {{slug: {slug}, name: \"{name}\"}}   # {count} open roles{note}")
    return 0 if found_any else 1


if __name__ == "__main__":
    sys.exit(main())
