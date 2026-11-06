"""Count my Claude Code tokens, estimate the energy they took, and update the summary in README.md.

Reads the usage that Claude Code records with every response in its local logs (~/.claude/projects/**/*.jsonl), adds
it up by day and model into data/daily.csv, turns tokens into energy with the assumptions in tracker/assumptions.json,
and rewrites the summary section of README.md and the chart in charts/daily.svg. Only daily totals per model leave the
logs: no prompts, no replies, no file or project names.

Claude Code deletes old logs after a while (30 days by default), so data/daily.csv keeps every day it has seen: run
this regularly and commit, and the history builds up.

Usage:  python tracker/update.py
"""
import csv, datetime as dt, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = json.load(open(os.path.join(ROOT, "tracker", "assumptions.json"), encoding="utf-8"))
FIELDS = ["date", "model", "responses", "input", "cache_write", "cache_read", "output"]
LEVELS = ("low", "central", "high")


def logs_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def collect():
    """{(date, model): totals} from the logs. A response can be logged more than once as it streams in; each is
    counted once, at its largest output."""
    seen = {}
    for path in glob.glob(os.path.join(logs_dir(), "**", "*.jsonl"), recursive=True):
        try: fh = open(path, encoding="utf-8")
        except OSError: continue
        with fh:
            for line in fh:
                if '"usage"' not in line: continue
                try: d = json.loads(line)
                except ValueError: continue
                m = d.get("message")
                if d.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict): continue
                model = m.get("model") or ""
                if not model.startswith("claude"): continue   # "<synthetic>" placeholders and the like use no tokens
                key = m.get("id") or d.get("requestId") or d.get("uuid")
                u = m["usage"]
                rec = {"input": u.get("input_tokens") or 0, "cache_write": u.get("cache_creation_input_tokens") or 0,
                       "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}
                ts = d.get("timestamp")
                if not ts: continue
                day = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()   # my local day
                old = seen.get(key)
                if old is None or rec["output"] > old[2]["output"]: seen[key] = (day, model, rec)
    days = {}
    for day, model, rec in seen.values():
        t = days.setdefault((day, model), {"responses": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0})
        t["responses"] += 1
        for k in rec: t[k] += rec[k]
    return days


def merge(new):
    """data/daily.csv plus the logs: a day keeps whichever count is larger, so days whose logs are gone stay."""
    path = os.path.join(ROOT, "data", "daily.csv")
    rows = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            rows[(r["date"], r["model"])] = {k: int(r[k]) for k in FIELDS[2:]}
    for k, t in new.items():
        if k not in rows or sum(t[f] for f in FIELDS[3:]) >= sum(rows[k][f] for f in FIELDS[3:]): rows[k] = t
    return rows


def wh_per_dollar():
    """Watt-hours per list-price dollar of tokens, from the anchor (a typical chatbot answer)."""
    a = A["anchor"]; pin, pout = A["prices"][a["model"]]
    dollars = (a["input_tokens"] * pin + a["output_tokens"] * pout) / 1e6
    return {lv: a["wh"][lv] / dollars for lv in LEVELS}


def dollars(model, t):
    pin, pout = A["prices"].get(model) or A["prices"][A["fallback_model"]]
    return (t["input"] * pin + t["cache_write"] * pin * A["cache_write_factor"] + t["cache_read"] * pin * A["cache_read_factor"]
            + t["output"] * pout) / 1e6


def fmt_wh(wh):
    if wh >= 1000: return f"{wh / 1000:,.2f} kWh" if wh < 100000 else f"{wh / 1000:,.0f} kWh"
    return f"{wh:,.1f} Wh" if wh < 100 else f"{wh:,.0f} Wh"


def fmt_n(n):
    for div, unit in ((1e9, " billion"), (1e6, " million"), (1e3, " thousand")):
        if n >= div: return f"{n / div:,.1f}{unit}"
    return f"{n:,}"


def like(wh):
    """The energy as everyday things, picking the comparisons that give friendly numbers."""
    out = []
    for c in A["comparisons"]:
        x = wh / c["wh"]
        if 0.5 <= x <= 2000: out.append((abs(__import__("math").log10(x) - 1), x, c))
    out.sort(key=lambda o: o[0])
    lines = []
    for _, x, c in sorted(out[:4], key=lambda o: o[2]["wh"]):
        n = f"{x:,.0f}" if x >= 10 else f"{x:,.1f}"
        lines.append(f"{n} × {c['name']}")
    return lines


def chart(daily, path):
    """Daily energy (central estimate, with the low to high range) for the last 60 days, as an SVG."""
    if not daily: return
    last = max(daily); first = max(min(daily), (dt.date.fromisoformat(last) - dt.timedelta(days=59)).isoformat())
    days = []
    d = dt.date.fromisoformat(first)
    while d.isoformat() <= last: days.append(d.isoformat()); d += dt.timedelta(days=1)
    vals = [daily.get(x, {"low": 0, "central": 0, "high": 0}) for x in days]
    top = max(v["high"] for v in vals) or 1
    W, H, L, B, T = 760, 260, 56, 34, 16
    bw = (W - L - 10) / len(days)
    y = lambda v: T + (H - T - B) * (1 - v / top)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif" font-size="11">',
         '<style>.t{fill:#57606a}.g{stroke:#d0d7de}.b{fill:#2da44e}.r{stroke:#1a7f37;stroke-width:1.2}@media (prefers-color-scheme:dark){.t{fill:#8b949e}.g{stroke:#30363d}.b{fill:#3fb950}.r{stroke:#56d364}}</style>']
    for k in range(5):
        v = top * k / 4
        s.append(f'<line class="g" x1="{L}" x2="{W - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text class="t" x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{fmt_wh(v)}</text>')
    for i, (day, v) in enumerate(zip(days, vals)):
        x = L + i * bw
        if v["central"] > 0:
            s.append(f'<rect class="b" x="{x + bw * 0.15:.1f}" y="{y(v["central"]):.1f}" width="{bw * 0.7:.1f}" height="{y(0) - y(v["central"]):.1f}"><title>{day}: {fmt_wh(v["central"])} ({fmt_wh(v["low"])} to {fmt_wh(v["high"])})</title></rect>')
            s.append(f'<line class="r" x1="{x + bw / 2:.1f}" x2="{x + bw / 2:.1f}" y1="{y(v["low"]):.1f}" y2="{y(v["high"]):.1f}"/>')
        if i % 7 == 0: s.append(f'<text class="t" x="{x + bw / 2:.1f}" y="{H - 12}" text-anchor="middle">{day[5:]}</text>')
    s.append("</svg>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(s))


def write_weekly(rows, k):
    """data/weekly.json, for the dashboard: energy per week (Monday to Sunday), its range, and where it went."""
    weeks = {}
    for (day, model), t in rows.items():
        d = dt.date.fromisoformat(day); wk = (d - dt.timedelta(days=d.weekday())).isoformat()
        w = weeks.setdefault(wk, {"week": wk, "days": set(), "responses": 0, "tokens": {f: 0 for f in FIELDS[3:]},
                                  "wh": {lv: 0.0 for lv in LEVELS}, "wh_by_type": {f: 0.0 for f in FIELDS[3:]}, "wh_by_model": {}})
        usd = dollars(model, t)
        w["days"].add(day); w["responses"] += t["responses"]
        for f in FIELDS[3:]:
            w["tokens"][f] += t[f]
            w["wh_by_type"][f] += dollars(model, {g: (t[g] if g == f else 0) for g in FIELDS[3:]}) * k["central"]
        for lv in LEVELS: w["wh"][lv] += usd * k[lv]
        w["wh_by_model"][model] = w["wh_by_model"].get(model, 0.0) + usd * k["central"]
    out = []
    for wk in sorted(weeks):
        w = weeks[wk]; w["days_used"] = len(w.pop("days"))
        w["wh"] = {lv: round(v, 1) for lv, v in w["wh"].items()}
        w["wh_by_type"] = {f: round(v, 1) for f, v in w["wh_by_type"].items()}
        w["wh_by_model"] = {m_: round(v, 1) for m_, v in sorted(w["wh_by_model"].items(), key=lambda kv: -kv[1])}
        out.append(w)
    data = {"updated": dt.date.today().isoformat(), "anchor": A["anchor"], "comparisons": A["comparisons"], "weeks": out}
    with open(os.path.join(ROOT, "data", "weekly.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)


def main():
    rows = merge(collect())
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    k = wh_per_dollar()
    with open(os.path.join(ROOT, "data", "daily.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(FIELDS + [f"wh_{lv}" for lv in LEVELS])
        for (day, model), t in sorted(rows.items()):
            usd = dollars(model, t)
            w.writerow([day, model] + [t[f] for f in FIELDS[2:]] + [round(usd * k[lv], 3) for lv in LEVELS])
    # totals
    daily, models, tok = {}, {}, {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    by_type = {f: 0.0 for f in tok}   # central Wh by kind of token
    for (day, model), t in rows.items():
        usd = dollars(model, t)
        for f in tok: by_type[f] += dollars(model, {g: (t[g] if g == f else 0) for g in tok}) * k["central"]
        dd = daily.setdefault(day, {lv: 0.0 for lv in LEVELS})
        for lv in LEVELS: dd[lv] += usd * k[lv]
        mm = models.setdefault(model, {"responses": 0, "tokens": 0, "wh": 0.0})
        mm["responses"] += t["responses"]; mm["tokens"] += sum(t[f] for f in tok); mm["wh"] += usd * k["central"]
        for f in tok: tok[f] += t[f]
    if not daily: print("No Claude Code usage found in", logs_dir()); return
    total = {lv: sum(d[lv] for d in daily.values()) for lv in LEVELS}
    today = dt.date.today()
    last30 = {lv: sum(v[lv] for d, v in daily.items() if d > (today - dt.timedelta(days=30)).isoformat()) for lv in LEVELS}
    active = len(daily); per_day = {lv: total[lv] / active for lv in LEVELS}
    first, last = min(daily), max(daily)
    chart(daily, os.path.join(ROOT, "charts", "daily.svg"))
    md = [f"*Updated {today:%d %B %Y}, covering {first} to {last} ({active} days with use).*", "",
          "| | Low | **Central** | High |", "|---|---|---|---|",
          f"| All time | {fmt_wh(total['low'])} | **{fmt_wh(total['central'])}** | {fmt_wh(total['high'])} |",
          f"| Last 30 days | {fmt_wh(last30['low'])} | **{fmt_wh(last30['central'])}** | {fmt_wh(last30['high'])} |",
          f"| An average day with use | {fmt_wh(per_day['low'])} | **{fmt_wh(per_day['central'])}** | {fmt_wh(per_day['high'])} |", "",
          f"**All time, the central estimate is about the same energy as:** " + "; ".join(like(total["central"])) + ".", "",
          f"**An average day of use is about:** " + "; ".join(like(per_day["central"])) + ".", "",
          "![Energy per day, last 60 days](charts/daily.svg)", "",
          "*Bars: central estimate per day. Lines: low to high.*", "",
          f"Tokens so far: {fmt_n(tok['output'])} output (including thinking), {fmt_n(tok['input'])} fresh input, "
          f"{fmt_n(tok['cache_write'])} written to the prompt cache and {fmt_n(tok['cache_read'])} read back from it.", "",
          "**Where the energy goes:** " + "; ".join(f"{100 * by_type[f] / sum(by_type.values()):.0f}% {label}" for f, label in
              (("cache_read", "re-reading the conversation so far (cache reads)"), ("output", "writing replies and thinking (output)"),
               ("cache_write", "adding new context to the cache"), ("input", "fresh input"))) + ". "
          "Long sessions re-read a long history with every reply, so starting fresh conversations (or `/clear`) is the biggest saving.", "",
          "| Model | Responses | Tokens | Energy (central) |", "|---|---|---|---|"]
    for name, mm in sorted(models.items(), key=lambda kv: -kv[1]["wh"]):
        md.append(f"| `{name}` | {mm['responses']:,} | {fmt_n(mm['tokens'])} | {fmt_wh(mm['wh'])} |")
    write_weekly(rows, k)
    readme = os.path.join(ROOT, "README.md")
    text = open(readme, encoding="utf-8").read()
    a, b = "<!-- summary:start -->", "<!-- summary:end -->"
    text = text[:text.index(a) + len(a)] + "\n" + "\n".join(md) + "\n" + text[text.index(b):]
    open(readme, "w", encoding="utf-8", newline="\n").write(text)
    print(f"{active} days, {fmt_wh(total['central'])} central ({fmt_wh(total['low'])} to {fmt_wh(total['high'])}).")


if __name__ == "__main__":
    sys.exit(main())
