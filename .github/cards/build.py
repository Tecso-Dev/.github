#!/usr/bin/env python3
"""Build TECSO's animated 3D org-profile cards from live GitHub data.

Usage: GITHUB_TOKEN=... python3 build.py --org Tecso-Dev --out dist
Writes hero.svg, stats.svg, building.svg, city.svg and process.svg. Numbers come from
public repositories only: the org's own plus the TECSO products listed in EXTRA_REPOS.
Forks are counted from the day they were forked, so upstream history is never claimed.
"""
import argparse
import json
import math
import os
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

STUDIO = {
    "wordmark": "TECSO",
    "fa": "راهکارپرداز فن‌آوران هوشمند",
    "kicker": "SOFTWARE & INFRASTRUCTURE STUDIO · SINCE 2018",
    "lines": ["Enterprise web, applied AI, DevOps and networks —", "designed, built and handed over end-to-end."],
    "chips": ["Vue · Nuxt", "Go", "Python · FastAPI", "PostgreSQL", "Kubernetes", "LLM agents"],
    "founded": 2018,
}
# TECSO products that live on the founder's account.
EXTRA_REPOS = [
    "sobhanaz/khodrobin",
    "sobhanaz/bobres-3x-ui-telegram-panel",
    "sobhanaz/sorinflow-sms-forwarder",
    "sobhanaz/digikala-clone",
    "sobhanaz/parsi-text",
    "sobhanaz/Gardian-One",
]
# Profile and placeholder repos, not product work.
EXCLUDE = {"Tecso-Dev/.github", "Tecso-Dev/Tecso-Dev"}
BUILDING_DAYS = 90
BUILDING_ROWS = 5

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Inter',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'JetBrains Mono',Menlo,Consolas,monospace"
FA = "Vazirmatn,Tahoma,'Segoe UI','Noto Sans Arabic',sans-serif"

# GitSkins "midnight / aura" palette, shared with the founder's profile cards.
C = dict(
    bg0="#020617", bg1="#0f172a", text="#e0e7ff", soft="#cbd5e1", muted="#94a3b8",
    accent="#818cf8", accent2="#a5b4fc", purple="#be5bf6", cyan="#5beaf6", green="#22c55e",
    deep="#1e1b4b",
)

REPO_FIELDS = "nameWithOwner name isFork isPrivate createdAt pushedAt owner { login } primaryLanguage { name color }"


# ---------------------------------------------------------------- data

class GitHub:
    def __init__(self, token):
        self.token = token

    def query(self, q, variables=None):
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": q, "variables": variables or {}}).encode(),
            headers={"Authorization": f"bearer {self.token}", "User-Agent": "tecso-cards", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        if payload.get("errors"):
            sys.exit(f"GitHub API error: {payload['errors']}")
        return payload["data"]


def fetch(gh, org):
    data = gh.query(
        f"query($org: String!) {{ organization(login: $org) {{ repositories(privacy: PUBLIC, first: 100) {{ totalCount nodes {{ {REPO_FIELDS} }} }} }} }}",
        {"org": org},
    )["organization"]["repositories"]
    repos = {r["nameWithOwner"]: r for r in data["nodes"]}
    if EXTRA_REPOS:
        parts = []
        for i, full in enumerate(EXTRA_REPOS):
            owner, name = full.split("/")
            parts.append(f'r{i}: repository(owner: "{owner}", name: "{name}") {{ {REPO_FIELDS} }}')
        extra = gh.query("{ " + " ".join(parts) + " }")
        for r in extra.values():
            if r:
                repos[r["nameWithOwner"]] = r
    tracked = [r for k, r in repos.items() if k not in EXCLUDE and not r["isPrivate"]]

    year_ago = datetime.now(timezone.utc) - timedelta(days=365)
    for r in tracked:
        since = year_ago
        if r["isFork"]:  # only work done in the fork, never the upstream history
            since = max(since, datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")))
        r["commit_dates"] = commit_dates(gh, r["owner"]["login"], r["name"], since)
    return data["totalCount"], tracked


def commit_dates(gh, owner, name, since):
    dates, cursor = [], None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = (
            f'{{ repository(owner: "{owner}", name: "{name}") {{ defaultBranchRef {{ target {{ ... on Commit {{ '
            f'history(since: "{since.strftime("%Y-%m-%dT%H:%M:%SZ")}", first: 100{after}) {{ '
            f'pageInfo {{ hasNextPage endCursor }} nodes {{ committedDate }} }} }} }} }} }} }}'
        )
        ref = gh.query(q)["repository"]["defaultBranchRef"]
        if not ref:
            return dates
        hist = ref["target"]["history"]
        dates += [n["committedDate"][:10] for n in hist["nodes"]]
        if not hist["pageInfo"]["hasNextPage"]:
            return dates
        cursor = hist["pageInfo"]["endCursor"]


def summarize(public_repos, tracked):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    start -= timedelta(days=(start.weekday() + 1) % 7)  # weeks start on Sunday, like GitHub
    per_day = Counter(d for r in tracked for d in r["commit_dates"])
    days, cur = [], start
    while cur <= today:
        days.append(dict(date=cur.isoformat(), contributionCount=per_day.get(cur.isoformat(), 0),
                         week=(cur - start).days // 7, weekday=(cur.weekday() + 1) % 7))
        cur += timedelta(days=1)
    counts = [d["contributionCount"] for d in days]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)

    cutoff = (today - timedelta(days=BUILDING_DAYS)).isoformat()
    building = []
    for r in tracked:
        n = sum(1 for d in r["commit_dates"] if d >= cutoff)
        if n:
            lang = r["primaryLanguage"] or {}
            building.append(dict(name=r["name"], owner=r["owner"]["login"], commits=n,
                                 lang=lang.get("name") or "", color=lang.get("color") or C["accent"]))
    building.sort(key=lambda r: -r["commits"])
    last_push = max(r["pushedAt"] for r in tracked)

    return dict(
        total=sum(counts), active=sum(1 for c in counts if c), longest=longest,
        best=max(days, key=lambda d: d["contributionCount"]), days=days, weeks=days[-1]["week"] + 1,
        repos_public=public_repos, projects_active=len(building),
        building=building[:BUILDING_ROWS], building_total=sum(r["commits"] for r in building),
        last_push=datetime.fromisoformat(last_push.replace("Z", "+00:00")).strftime("%b %-d"),
        updated=today.isoformat(),
    )


# ---------------------------------------------------------------- svg helpers

def shade(hex_color, f):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * f))) for v in (r, g, b))


def pts(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def fmt(n):
    return f"{n:,}"


def frame(w, h, uid, body, title, updated=None):
    """Card shell: midnight gradient, drifting aura orbs, glass inner card, 'updated' stamp."""
    stamp = (
        f'<text x="{w - 34}" y="{h - 28}" text-anchor="end" font-family="{MONO}" font-size="10" '
        f'fill="{C["muted"]}" fill-opacity=".8">live from GitHub · updated {updated}</text>'
    ) if updated else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">
<title>{escape(title)}</title>
<defs>
  <linearGradient id="{uid}-bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="{C['bg0']}"/><stop offset="54%" stop-color="{C['bg1']}"/><stop offset="100%" stop-color="{C['bg0']}"/></linearGradient>
  <radialGradient id="{uid}-o1"><stop offset="0%" stop-color="{C['accent']}" stop-opacity=".75"/><stop offset="60%" stop-color="{C['accent']}" stop-opacity=".15"/><stop offset="100%" stop-color="{C['accent']}" stop-opacity="0"/></radialGradient>
  <radialGradient id="{uid}-o2"><stop offset="0%" stop-color="{C['purple']}" stop-opacity=".6"/><stop offset="60%" stop-color="{C['purple']}" stop-opacity=".13"/><stop offset="100%" stop-color="{C['purple']}" stop-opacity="0"/></radialGradient>
  <radialGradient id="{uid}-o3"><stop offset="0%" stop-color="{C['cyan']}" stop-opacity=".55"/><stop offset="60%" stop-color="{C['cyan']}" stop-opacity=".12"/><stop offset="100%" stop-color="{C['cyan']}" stop-opacity="0"/></radialGradient>
  <linearGradient id="{uid}-shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="{C['accent2']}" stop-opacity=".15"/><stop offset="45%" stop-color="{C['accent']}" stop-opacity=".85"/><stop offset="100%" stop-color="{C['purple']}" stop-opacity=".2"/></linearGradient>
  <linearGradient id="{uid}-word" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="{C['text']}"/><stop offset="55%" stop-color="{C['accent']}"/><stop offset="100%" stop-color="{C['purple']}"/></linearGradient>
  <linearGradient id="{uid}-sweep" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stop-color="#fff" stop-opacity="0"/><stop offset="50%" stop-color="#fff" stop-opacity=".16"/><stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <clipPath id="{uid}-clip"><rect width="{w}" height="{h}" rx="22"/></clipPath>
</defs>
<style>
  .{uid}-a{{animation:{uid}-fa 10s ease-in-out infinite}}
  .{uid}-b{{animation:{uid}-fb 12s ease-in-out infinite 1s}}
  .{uid}-c{{animation:{uid}-fc 14s ease-in-out infinite .5s}}
  .{uid}-float{{animation:{uid}-float 4.5s ease-in-out infinite}}
  .{uid}-bob{{animation:{uid}-bob 5s ease-in-out infinite}}
  .{uid}-pulse{{animation:{uid}-pulse 2.4s ease-in-out infinite}}
  .{uid}-sweep{{animation:{uid}-sweep 5.5s ease-in-out infinite}}
  .{uid}-wave{{opacity:0;animation:{uid}-wave 7s linear infinite}}
  .{uid}-twinkle{{animation:{uid}-twinkle 3.6s ease-in-out infinite}}
  @keyframes {uid}-fa{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(26px,-18px)}}}}
  @keyframes {uid}-fb{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(-22px,16px)}}}}
  @keyframes {uid}-fc{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(16px,-10px)}}}}
  @keyframes {uid}-float{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-9px)}}}}
  @keyframes {uid}-bob{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-3px)}}}}
  @keyframes {uid}-pulse{{0%,100%{{opacity:.35}}50%{{opacity:1}}}}
  @keyframes {uid}-sweep{{0%{{transform:translateX(-260px)}}60%,100%{{transform:translateX(1100px)}}}}
  @keyframes {uid}-wave{{0%{{opacity:0}}6%{{opacity:.55}}14%,100%{{opacity:0}}}}
  @keyframes {uid}-twinkle{{0%,100%{{opacity:.15}}50%{{opacity:.9}}}}
  @media (prefers-reduced-motion:reduce){{.{uid}-a,.{uid}-b,.{uid}-c,.{uid}-float,.{uid}-bob,.{uid}-pulse,.{uid}-sweep,.{uid}-wave,.{uid}-twinkle{{animation:none}}}}
</style>
<g clip-path="url(#{uid}-clip)">
  <rect width="{w}" height="{h}" fill="url(#{uid}-bg)"/>
  <g opacity=".55">
    <circle class="{uid}-a" cx="{w * .82:.0f}" cy="{h * .16:.0f}" r="{min(h * .62, 230):.0f}" fill="url(#{uid}-o1)"/>
    <circle class="{uid}-b" cx="{w * .1:.0f}" cy="{h * .95:.0f}" r="{min(h * .55, 210):.0f}" fill="url(#{uid}-o2)"/>
    <circle class="{uid}-c" cx="{w * .97:.0f}" cy="{h * .92:.0f}" r="{min(h * .42, 170):.0f}" fill="url(#{uid}-o3)"/>
  </g>
  <rect x="16" y="16" width="{w - 32}" height="{h - 32}" rx="18" fill="#fff" fill-opacity=".03" stroke="url(#{uid}-shine)" stroke-width="1.2"/>
{body}
  {stamp}
</g>
<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="21.5" fill="none" stroke="{C['accent']}" stroke-opacity=".35"/>
</svg>
"""


def extruded(x, y, text, size, uid, depth=5, anchor="start", family=SANS, weight=800, spacing=-1):
    """Text with a stacked 3D extrusion under a gradient face."""
    attrs = f'text-anchor="{anchor}" font-family="{family}" font-size="{size}" font-weight="{weight}" letter-spacing="{spacing}"'
    layers = [
        f'<text x="{x + i * .55:.2f}" y="{y + i * .85:.2f}" {attrs} fill="{shade(C["deep"], 1 - i * .08)}">{escape(text)}</text>'
        for i in range(depth, 0, -1)
    ]
    layers.append(f'<text x="{x}" y="{y}" {attrs} fill="url(#{uid}-word)">{escape(text)}</text>')
    return "".join(layers)


def kicker(x, y, text, anchor="start"):
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{MONO}" font-size="11" letter-spacing="3" fill="{C["muted"]}">{escape(text)}</text>'


def iso_cube(cx, cy, a, color, stroke):
    """Isometric cube whose top-front vertex sits at (cx, cy)."""
    k = .866 * a
    top = [(cx, cy - a), (cx + k, cy - a / 2), (cx, cy), (cx - k, cy - a / 2)]
    right = [(cx, cy), (cx + k, cy - a / 2), (cx + k, cy + a / 2), (cx, cy + a)]
    left = [(cx, cy), (cx, cy + a), (cx - k, cy + a / 2), (cx - k, cy - a / 2)]
    s = f'stroke="{stroke}" stroke-opacity=".55" stroke-width=".8" stroke-linejoin="round"'
    return (
        f'<polygon points="{pts(left)}" fill="{shade(color, .62)}" {s}/>'
        f'<polygon points="{pts(right)}" fill="{shade(color, .45)}" {s}/>'
        f'<polygon points="{pts(top)}" fill="{shade(color, 1.0)}" {s}/>'
    )


# ---------------------------------------------------------------- cards

def hero(s):
    w, h, uid = 860, 340, "h"
    p = STUDIO
    chips, x = [], 44
    for label in p["chips"]:
        cw = len(label) * 7.25 + 26
        chips.append(
            f'<rect x="{x:.1f}" y="282" width="{cw:.1f}" height="28" rx="14" fill="{C["accent"]}" fill-opacity=".12" stroke="{C["accent"]}" stroke-opacity=".35"/>'
            f'<text x="{x + cw / 2:.1f}" y="300.5" text-anchor="middle" font-family="{MONO}" font-size="12" fill="#c7d2fe">{escape(label)}</text>'
        )
        x += cw + 8

    top = s["building"][0] if s["building"] else None
    live = ""
    if top:
        label = f'{top["name"]} · {fmt(top["commits"])} commits in {BUILDING_DAYS} days'
        lw = 124 + len(label) * 7.1
        live = (
            f'<rect x="44" y="238" width="{lw:.0f}" height="30" rx="15" fill="{C["green"]}" fill-opacity=".08" stroke="{C["green"]}" stroke-opacity=".45"/>'
            f'<circle cx="62" cy="253" r="6" fill="{C["green"]}" fill-opacity=".25" class="{uid}-pulse"/><circle cx="62" cy="253" r="3.2" fill="{C["green"]}"/>'
            f'<text x="74" y="257" font-family="{MONO}" font-size="10.5" letter-spacing="1.5" fill="#86efac">NOW SHIPPING</text>'
            f'<text x="180" y="257.5" font-family="{MONO}" font-size="12" fill="{C["text"]}">{escape(label)}</text>'
        )

    # Isometric platform with one floating pillar per service line around a glowing core.
    a, ox, oy = 26, 702, 178
    def at(i, j, k):
        return ox + (i - j) * .866 * a, oy + (i + j) * a / 2 - k * a
    base = sorted(((i, j) for i in range(3) for j in range(3)), key=lambda c: c[0] + c[1])
    platform = "".join(iso_cube(*at(i, j, 0), a, "#312e81", C["accent2"]) for i, j in base)
    cx, cy = at(1, 1, 0)
    core = (
        f'<polygon class="{uid}-pulse" points="{pts([(cx, cy - a), (cx + .866 * a, cy - a / 2), (cx, cy), (cx - .866 * a, cy - a / 2)])}" fill="{C["cyan"]}" fill-opacity=".55"/>'
        f'<ellipse class="{uid}-pulse" cx="{cx}" cy="{cy - a / 2:.1f}" rx="30" ry="14" fill="none" stroke="{C["cyan"]}" stroke-opacity=".6"/>'
    )
    pillars = [((0, 0), 3, C["purple"], "ai"), ((2, 0), 2, C["accent"], "web"), ((0, 2), 2, C["cyan"], "ops"), ((2, 2), 1, "#6366f1", "net")]
    columns = []
    for (i, j), height, col, tag in sorted(pillars, key=lambda p: p[0][0] + p[0][1]):
        tx, ty = at(i, j, height + 1)
        columns.append(
            f'<g class="{uid}-bob" style="animation-delay:{-i * .7 - j * .4:.1f}s">'
            + "".join(iso_cube(*at(i, j, k), a, col, C["accent2"]) for k in range(1, height + 1))
            + f'<rect x="{tx - 19:.1f}" y="{ty - 16:.1f}" width="38" height="18" rx="9" fill="{C["bg1"]}" stroke="{col}" stroke-opacity=".8"/>'
            f'<text x="{tx:.1f}" y="{ty - 3:.1f}" text-anchor="middle" font-family="{MONO}" font-size="10.5" fill="{C["text"]}">{tag}</text></g>'
        )
    stack = platform + core + "".join(columns)
    stars = "".join(
        f'<circle class="{uid}-twinkle" style="animation-delay:{d}s" cx="{sx}" cy="{sy}" r="{r}" fill="{C["accent2"]}"/>'
        for sx, sy, r, d in [(588, 66, 1.4, 0), (818, 92, 1.2, 1.1), (596, 280, 1.1, 2.0), (826, 262, 1.5, .6), (764, 50, 1, 1.7)]
    )
    orbit = "M 548 196 a 154 44 0 1 0 308 0 a 154 44 0 1 0 -308 0"
    body = f"""  {kicker(44, 52, p['kicker'])}
  {extruded(40, 136, p['wordmark'], 84, uid, depth=8, spacing=-3)}
  <text x="44" y="176" font-family="{FA}" font-size="20" font-weight="600" fill="{C['soft']}">{escape(p['fa'])}</text>
  <text x="44" y="208" font-family="{SANS}" font-size="14.5" fill="{C['muted']}">{escape(p['lines'][0])}</text>
  <text x="44" y="228" font-family="{SANS}" font-size="14.5" fill="{C['muted']}">{escape(p['lines'][1])}</text>
  {live}
  {''.join(chips)}
  {stars}
  <path d="{orbit}" fill="none" stroke="{C['accent']}" stroke-opacity=".28" stroke-dasharray="2 7"/>
  <ellipse cx="{ox}" cy="{oy + 3 * a + 10:.0f}" rx="130" ry="30" fill="{C['accent']}" fill-opacity=".10"/>
  {stack}
  <circle r="4" fill="{C['cyan']}"><animateMotion dur="9s" repeatCount="indefinite" path="{orbit}"/></circle>
  <circle r="3" fill="{C['purple']}"><animateMotion dur="9s" begin="-4.5s" repeatCount="indefinite" path="{orbit}"/></circle>"""
    return frame(w, h, uid, body, "TECSO — software and infrastructure studio, Urmia, Iran, since 2018")


def stats(s):
    w, h, uid = 860, 222, "s"
    tiles = [
        ("FOUNDED", str(STUDIO["founded"]), "Urmia, Iran", C["accent"]),
        ("COMMITS", fmt(s["total"]), "last 12 months", C["purple"]),
        ("ACTIVE PROJECTS", fmt(s["projects_active"]), f"pushed in {BUILDING_DAYS} days", C["cyan"]),
        ("PUBLIC REPOS", fmt(s["repos_public"]), "on Tecso-Dev", C["accent2"]),
        ("LAST SHIPPED", s["last_push"], "latest push", "#f0abfc"),
    ]
    tw, th, depth, gap = 146, 100, 9, 16
    x0 = (w - (5 * tw + 4 * gap + depth)) / 2
    out = [kicker(44, 48, "TECSO ON GITHUB · PUBLIC REPOSITORIES")]
    for i, (label, value, cap, color) in enumerate(tiles):
        x, y = x0 + i * (tw + gap), 66
        cid = f"{uid}-t{i}"
        out.append(
            f'<clipPath id="{cid}"><rect x="{x:.1f}" y="{y}" width="{tw}" height="{th}" rx="10"/></clipPath>'
            f'<g class="{uid}-bob" style="animation-delay:{-i * .6:.1f}s">'
            f'<polygon points="{pts([(x + tw - 6, y + 4), (x + tw + depth, y + depth + 4), (x + tw + depth, y + th + depth - 6), (x + tw - 6, y + th - 4)])}" fill="{shade(color, .32)}"/>'
            f'<polygon points="{pts([(x + 6, y + th - 2), (x + tw - 4, y + th - 2), (x + tw + depth - 2, y + th + depth), (x + depth + 4, y + th + depth)])}" fill="{shade(color, .22)}"/>'
            f'<rect x="{x:.1f}" y="{y}" width="{tw}" height="{th}" rx="10" fill="{C["bg1"]}" stroke="{color}" stroke-opacity=".55"/>'
            f'<rect x="{x:.1f}" y="{y}" width="{tw}" height="{th}" rx="10" fill="{color}" fill-opacity=".07"/>'
            f'<g clip-path="url(#{cid})"><rect class="{uid}-sweep" style="animation-delay:{i * .35:.2f}s" x="{x - 120:.1f}" y="{y}" width="110" height="{th}" fill="url(#{uid}-sweep)"/></g>'
            f'<text x="{x + 14:.1f}" y="{y + 24}" font-family="{MONO}" font-size="9.5" letter-spacing="1.8" fill="{C["muted"]}">{label}</text>'
            f'{extruded(x + 13, y + 64, value, 32, uid, depth=4, spacing=-.5)}'
            f'<text x="{x + 14:.1f}" y="{y + 86}" font-family="{SANS}" font-size="11.5" fill="{C["soft"]}">{escape(cap)}</text>'
            f'</g>'
        )
    return frame(w, h, uid, "  " + "\n  ".join(out), f"TECSO: founded 2018, {fmt(s['total'])} public commits in the last 12 months, {s['projects_active']} active projects", s["updated"])


def building(s):
    rows = s["building"]
    w, uid = 860, "b"
    h = 140 + max(1, len(rows)) * 48 + 30
    out = [
        kicker(44, 50, f"WHAT WE'RE BUILDING · LAST {BUILDING_DAYS} DAYS"),
        f'<text x="44" y="80" font-family="{SANS}" font-size="20" font-weight="700" fill="{C["text"]}">Public commits by project</text>',
        f'<text x="{w - 44}" y="80" text-anchor="end" font-family="{MONO}" font-size="13" fill="{C["accent2"]}">{fmt(s["building_total"])} commits</text>',
    ]
    top = max((r["commits"] for r in rows), default=1)
    bx, bmax, depth = 330, 380, 7
    for i, r in enumerate(rows):
        y = 112 + i * 48
        length = max(12, bmax * r["commits"] / top)
        color = r["color"]
        front = [(bx, y + 8), (bx + length, y + 8), (bx + length, y + 28), (bx, y + 28)]
        topf = [(bx, y + 8), (bx + depth, y + 8 - depth), (bx + length + depth, y + 8 - depth), (bx + length, y + 8)]
        side = [(bx + length, y + 8), (bx + length + depth, y + 8 - depth), (bx + length + depth, y + 28 - depth), (bx + length, y + 28)]
        cid = f"{uid}-r{i}"
        name = r["name"] if len(r["name"]) <= 30 else r["name"][:29] + "…"
        out.append(
            f'<text x="44" y="{y + 18}" font-family="{SANS}" font-size="14.5" font-weight="700" fill="{C["text"]}">{escape(name)}</text>'
            f'<circle cx="49" cy="{y + 32}" r="4" fill="{color}"/>'
            f'<text x="59" y="{y + 36}" font-family="{MONO}" font-size="11" fill="{C["muted"]}">{escape(r["owner"])}{(" · " + escape(r["lang"])) if r["lang"] else ""}</text>'
            f'<polygon points="{pts(topf)}" fill="{shade(color, 1.15)}" fill-opacity=".95"/>'
            f'<polygon points="{pts(side)}" fill="{shade(color, .5)}"/>'
            f'<polygon points="{pts(front)}" fill="{shade(color, .8)}"/>'
            f'<clipPath id="{cid}"><polygon points="{pts(front)}"/></clipPath>'
            f'<g clip-path="url(#{cid})"><rect class="{uid}-sweep" style="animation-delay:{i * .4:.1f}s" x="{bx - 120}" y="{y + 8}" width="110" height="20" fill="url(#{uid}-sweep)"/></g>'
            f'<text x="{bx + length + depth + 12:.1f}" y="{y + 24}" font-family="{MONO}" font-size="13" font-weight="700" fill="{C["text"]}">{fmt(r["commits"])}</text>'
        )
    if not rows:
        out.append(f'<text x="44" y="130" font-family="{SANS}" font-size="14" fill="{C["muted"]}">No public commits in this window.</text>')
    out.append(f'<text x="44" y="{h - 28}" font-family="{MONO}" font-size="10" fill="{C["muted"]}" fill-opacity=".8">Public repositories only · SorinFlow and client projects are private</text>')
    return frame(w, h, uid, "  " + "\n  ".join(out), f"What TECSO is building: public commits by project in the last {BUILDING_DAYS} days", s["updated"])


def city(s):
    w, h, uid = 860, 452, "c"
    days = s["days"]
    nonzero = sorted(d["contributionCount"] for d in days if d["contributionCount"])
    top = nonzero[-1] if nonzero else 1
    q = [nonzero[int(len(nonzero) * f)] for f in (.25, .5, .75)] if nonzero else [1, 1, 1]
    levels = ["#3730a3", "#4f46e5", "#8b5cf6", "#22d3ee"]

    ux, uy, vx, vy = 12.2, 3.0, -8.0, 6.2
    weeks = s["weeks"]
    x0 = (w - (weeks * ux + 7 * -vx)) / 2 + 7 * -vx
    y0 = 184

    def base(wk, d):
        return x0 + wk * ux + d * vx, y0 + wk * uy + d * vy

    f0, f1, f2, f3 = base(-.6, -.6), base(weeks + .2, -.6), base(weeks + .2, 7.2), base(-.6, 7.2)
    out = [
        kicker(44, 50, "STUDIO ACTIVITY · LAST 12 MONTHS"),
        f'<text x="44" y="80" font-family="{SANS}" font-size="20" font-weight="700" fill="{C["text"]}">{fmt(s["total"])} public commits, one tower per day</text>',
        f'<polygon points="{pts([f0, f1, f2, f3])}" fill="{C["accent"]}" fill-opacity=".05" stroke="{C["accent"]}" stroke-opacity=".25"/>',
    ]
    shrink = .84
    cells = sorted(days, key=lambda d: (d["week"] * uy + d["weekday"] * vy, d["week"]))
    for d in cells:
        c = d["contributionCount"]
        bx, by = base(d["week"], d["weekday"])
        p0, p1 = (bx, by), (bx + ux * shrink, by + uy * shrink)
        p2, p3 = (bx + (ux + vx) * shrink, by + (uy + vy) * shrink), (bx + vx * shrink, by + vy * shrink)
        if not c:
            out.append(f'<polygon points="{pts([p0, p1, p2, p3])}" fill="{C["accent"]}" fill-opacity=".09"/>')
            continue
        color = levels[sum(c > t for t in q)]
        hgt = 5 + 72 * math.log1p(c) / math.log1p(top)
        up = lambda p: (p[0], p[1] - hgt)
        out.append(
            f'<polygon points="{pts([p3, p2, up(p2), up(p3)])}" fill="{shade(color, .62)}"/>'
            f'<polygon points="{pts([p1, p2, up(p2), up(p1)])}" fill="{shade(color, .45)}"/>'
            f'<polygon points="{pts([up(p0), up(p1), up(p2), up(p3)])}" fill="{color}"/>'
            f'<polygon class="{uid}-wave" style="animation-delay:{d["week"] * .1 - 7:.2f}s" points="{pts([up(p0), up(p1), up(p2), up(p3)])}" fill="#fff"/>'
        )
        if d is s["best"]:
            bxp, byp = up(p0)[0] + (ux + vx) * shrink / 2, up(p0)[1] + (uy + vy) * shrink / 2
            out.append(
                f'<line x1="{bxp:.1f}" y1="{byp - 4:.1f}" x2="{bxp:.1f}" y2="{byp - 26:.1f}" stroke="{C["cyan"]}" stroke-opacity=".8"/>'
                f'<circle cx="{bxp:.1f}" cy="{byp - 28:.1f}" r="3" fill="{C["cyan"]}" class="{uid}-pulse"/>'
                f'<text x="{bxp:.1f}" y="{byp - 36:.1f}" text-anchor="middle" font-family="{MONO}" font-size="10.5" fill="{C["cyan"]}">busiest day · {c}</text>'
            )

    seen = None
    for d in days:
        if d["weekday"] != 0:
            continue
        m = d["date"][:7]
        if m != seen and d["week"] < weeks - 2:
            seen = m
            lx, ly = base(d["week"], 7.6)
            out.append(f'<text x="{lx:.1f}" y="{ly + 12:.1f}" font-family="{MONO}" font-size="10" fill="{C["muted"]}">{datetime.strptime(m, "%Y-%m").strftime("%b")}</text>')

    best = s["best"]
    best_label = datetime.strptime(best["date"], "%Y-%m-%d").strftime("%b %-d")
    facts = f'{fmt(s["active"])} active days · longest streak {s["longest"]} days · busiest day {best["contributionCount"]} commits on {best_label} · updated {s["updated"]}'
    out.append(f'<text x="44" y="{h - 28}" font-family="{MONO}" font-size="10.5" fill="{C["soft"]}">{escape(facts)}</text>')
    legend_x = w - 44 - 4 * 18 - 60
    out.append(f'<text x="{legend_x - 8}" y="78" text-anchor="end" font-family="{MONO}" font-size="10" fill="{C["muted"]}">less</text>')
    for i, col in enumerate(levels):
        out.append(f'<rect x="{legend_x + i * 18}" y="68" width="13" height="13" rx="3" fill="{col}"/>')
    out.append(f'<text x="{legend_x + 4 * 18 + 4}" y="78" font-family="{MONO}" font-size="10" fill="{C["muted"]}">more</text>')
    return frame(w, h, uid, "  " + "\n  ".join(out), f"TECSO studio activity: {fmt(s['total'])} public commits in the last 12 months as 3D towers")


def process(_s):
    w, h, uid = 860, 300, "p"
    steps = [
        ("Written scope", "estimate + SRS, signed"),
        ("Build in your repo", "your Git from day one"),
        ("Weekly demo", "staging + short report"),
        ("Test-gated deploy", "CI, rollback, backups"),
        ("Hand-over", "code, docs, access"),
    ]
    colors = [C["accent"], C["accent2"], C["purple"], C["cyan"], "#6366f1"]
    bw, gap, depth, baseline = 118, 40, 16, 214
    x0 = (w - (5 * bw + 4 * gap + depth)) / 2
    out = [
        kicker(44, 50, "HOW A PROJECT RUNS"),
        f'<text x="44" y="80" font-family="{SANS}" font-size="20" font-weight="700" fill="{C["text"]}">Five steps up, from a written scope to a clean hand-over</text>',
    ]
    tops = []
    for i, ((title, cap), col) in enumerate(zip(steps, colors)):
        x, hgt = x0 + i * (bw + gap), 34 + i * 17
        y = baseline - hgt
        front = [(x, y), (x + bw, y), (x + bw, baseline), (x, baseline)]
        topf = [(x, y), (x + depth, y - depth * .7), (x + bw + depth, y - depth * .7), (x + bw, y)]
        side = [(x + bw, y), (x + bw + depth, y - depth * .7), (x + bw + depth, baseline - depth * .7), (x + bw, baseline)]
        cid = f"{uid}-s{i}"
        tops.append((x + bw / 2 + depth / 2, y - depth * .35))
        out.append(
            f'<polygon points="{pts(side)}" fill="{shade(col, .38)}"/>'
            f'<polygon points="{pts(topf)}" fill="{shade(col, .95)}"/>'
            f'<polygon points="{pts(front)}" fill="{shade(col, .3)}" stroke="{col}" stroke-opacity=".6"/>'
            f'<clipPath id="{cid}"><polygon points="{pts(front)}"/></clipPath>'
            f'<g clip-path="url(#{cid})"><rect class="{uid}-sweep" style="animation-delay:{i * .3:.1f}s" x="{x - 120:.1f}" y="{y}" width="110" height="{hgt}" fill="url(#{uid}-sweep)"/></g>'
            f'<text x="{x + 12:.1f}" y="{y + 22:.1f}" font-family="{MONO}" font-size="13" font-weight="700" fill="{C["text"]}">0{i + 1}</text>'
            f'<text x="{x + bw / 2:.1f}" y="{baseline + 26}" text-anchor="middle" font-family="{SANS}" font-size="13.5" font-weight="700" fill="{C["text"]}">{title}</text>'
            f'<text x="{x + bw / 2:.1f}" y="{baseline + 44}" text-anchor="middle" font-family="{SANS}" font-size="11.5" fill="{C["muted"]}">{cap}</text>'
        )
    path = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in tops)
    out.append(f'<path d="{path}" fill="none" stroke="{C["cyan"]}" stroke-opacity=".35" stroke-dasharray="3 6"/>')
    out.append(f'<circle r="5" fill="{C["cyan"]}"><animateMotion dur="5s" repeatCount="indefinite" path="{path}"/></circle>')
    return frame(w, h, uid, "  " + "\n  ".join(out), "How a TECSO project runs: written scope, build in your repo, weekly demo, test-gated deploy, hand-over")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    s = summarize(*fetch(GitHub(token), args.org))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("hero", hero), ("stats", stats), ("building", building), ("city", city), ("process", process)):
        (out / f"{name}.svg").write_text(fn(s), encoding="utf-8")
    print(json.dumps({k: v for k, v in s.items() if k != "days"}, default=str, indent=1))


if __name__ == "__main__":
    main()
