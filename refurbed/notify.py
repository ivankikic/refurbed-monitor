"""
State (seen.json dedup) + alert rendering (plain text + HTML table) + delivery.

Dedup is per CONFIG identity (no price). A config alerts only when NEW or when
its price drops meaningfully — see compute_alerts(). The email is sent as
multipart: an HTML comparison table (what Gmail shows) + a plain-text fallback.
"""
from __future__ import annotations

import html as ihtml
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from . import config
from .analyze import Anomaly, Offer, Report, best_per_machine, value_score

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seen.json")
HISTORY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "history.jsonl")
SEEN_TTL_DAYS = 30


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_seen(path: str = STATE_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}
    return {}


def save_seen(seen: dict, path: str = STATE_PATH) -> None:
    # prune entries older than SEEN_TTL_DAYS (values are {"p": price, "t": iso})
    now = datetime.now(timezone.utc)
    pruned = {}
    for key, rec in seen.items():
        iso = rec.get("t") if isinstance(rec, dict) else None
        try:
            age = (now - datetime.fromisoformat(iso)).days if iso else 0
        except (ValueError, TypeError):
            age = 0
        if isinstance(rec, dict) and age <= SEEN_TTL_DAYS:
            pruned[key] = rec
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, ensure_ascii=False, indent=1, sort_keys=True)


# --------------------------------------------------------------------------- #
# Dedup keys — CONFIG identity (NO price). Re-alert only on a real price drop.
# --------------------------------------------------------------------------- #
def offer_key(o: Offer) -> str:
    return (f"OFF|{o.product}|{o.ram}/{o.storage}|{o.color}|{o.condition}"
            f"|{o.keyboard}|{o.battery}")


def path_key(spec: tuple) -> str:
    return f"PATH|{spec[0]}/{spec[1]}"


def anom_key(a) -> str:
    u = a.upgrade
    return (f"ANOM|{a.kind}|{a.product}|{u.ram}/{u.storage}|{u.color}"
            f"|{u.condition}|{u.battery}")


def current_state(report: Report) -> dict:
    """key -> {price, label} for everything worth alerting on this run.
    Cheapest price wins per key."""
    cur: dict[str, dict] = {}

    def put(key: str, price: float, label: str):
        if key not in cur or price < cur[key]["price"]:
            cur[key] = {"price": price, "label": label}

    for o in report.picks:
        put(offer_key(o), o.price, f"PICK {o.model} {o.spec_label} — {o.price:.2f} €")
    for o in report.deals:
        put(offer_key(o), o.price, f"DEAL {o.model} {o.spec_label} — {o.price:.2f} €")
    for spec, o in report.paths.items():
        if o is not None:
            put(path_key(spec), o.price,
                f"PATH {spec[0]}/{spec[1]} → {o.model} — {o.price:.2f} €")
    for a in report.anomalies:
        put(anom_key(a), a.upgrade.price, f"ANOM {a.text}")
    return cur


def compute_alerts(current: dict, seen: dict) -> tuple:
    """Return (alert_keys, new_seen). A key alerts if it's NEW or its price
    dropped meaningfully vs the price we last alerted on; tiny wiggles and price
    rises never alert (so you're not spammed with the same offer)."""
    drop_pct = getattr(config, "REALERT_DROP_PCT", 3.0)
    drop_abs = getattr(config, "REALERT_DROP_ABS", 20.0)
    now = now_iso()
    alerts: set = set()
    new_seen: dict = {}
    for key, info in current.items():
        price = info["price"]
        prev = seen.get(key)
        prev_price = prev.get("p") if isinstance(prev, dict) else None
        if prev_price is None:                       # brand-new config/anomaly
            alerts.add(key)
            new_seen[key] = {"p": price, "t": now}
        elif price <= prev_price - max(drop_abs, prev_price * drop_pct / 100):
            alerts.add(key)                          # genuine price drop
            new_seen[key] = {"p": price, "t": now}
        else:                                        # unchanged / wiggle / pricier
            new_seen[key] = {"p": prev_price, "t": now}   # keep reference price
    # carry over still-tracked configs not seen this run (e.g. temporarily sold
    # out) so they don't re-alert at the same price when they return
    for key, prev in seen.items():
        if key not in new_seen and isinstance(prev, dict):
            new_seen[key] = prev
    return alerts, new_seen


# --------------------------------------------------------------------------- #
# New-this-run offers + append-only history log (for later tuning)
# --------------------------------------------------------------------------- #
def new_offers(report: Report, alert_keys: set) -> list[Offer]:
    """Offers that are genuinely NEW/cheaper this run (one per machine, best
    variant, ranked) — the actual reason an email is being sent."""
    fresh = [o for o in report.offers if offer_key(o) in alert_keys]
    fresh = best_per_machine(fresh)
    fresh.sort(key=value_score, reverse=True)
    return fresh


def _pick_record(o: Offer) -> dict:
    return {"model": o.model, "ram": o.ram, "storage": o.storage, "color": o.color,
            "condition": o.condition, "battery": o.battery, "keyboard": o.keyboard,
            "price": round(o.price, 2), "discount_pct": o.discount_pct,
            "vs_baseline_pct": o.vs_baseline_pct, "all_time_low": o.all_time_low,
            "available": o.available, "url": o.url, "key": offer_key(o)}


def append_history(report: Report, alert_keys: set, mode: str, ts: str,
                   emitted: bool, path: str = HISTORY_PATH) -> None:
    """Append one JSON line per run so we can later analyse real results and tune
    the algorithm. Quiet runs log a slim record."""
    rec = {
        "ts": ts, "mode": mode, "emitted": emitted,
        "n_offers": len(report.offers),
        "n_available": sum(1 for o in report.offers if o.available),
        "n_alerts": len(alert_keys),
        "n_anomalies": len(report.anomalies),
        "picks": [_pick_record(o) for o in report.picks],
        "new": [_pick_record(o) for o in new_offers(report, alert_keys)],
        "underpriced": [_pick_record(o) for o in report.underpriced[:10]],
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"  [history] could not append: {exc}")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _wrap(text: str, width: int = 64) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width)) or text


def _baseline_note(o: Offer) -> str:
    """' ↘ inače ~1350€ (−26%) · 🏆 najniža dosad' when we have a baseline."""
    if not o.baseline_median:
        return ""
    parts = []
    if o.vs_baseline_pct >= 1:
        parts.append(f"inače ~{o.baseline_median:.0f}€ (−{o.vs_baseline_pct:.0f}%)")
    if o.all_time_low:
        parts.append("🏆 najniža dosad")
    return " · ".join(parts)


def _fmt_offer_line(o: Offer) -> str:
    av = "" if o.available else "  (RASPRODANO)"
    kb = f" {o.keyboard}" if o.keyboard else ""
    disc = f" −{o.discount_pct:.0f}%" if o.discount_pct else ""
    return (f"  • {o.price:>8.2f} €{disc}  {o.model}  [{o.ram}GB/{o.storage}GB "
            f"{o.color} {o.condition}{kb}]{av}\n    {o.url}")


_TIER_EMOJI = {"STEAL": "🔥", "GREAT": "🟢", "GOOD": "⚪"}


def _deterministic_tier(o: Offer) -> str:
    dream = getattr(config, "DREAM_DISCOUNT_PCT", 40)
    if o.discount_pct >= dream:
        return "STEAL"
    if o.discount_pct >= 25:
        return "GREAT"
    return "GOOD"


def picks_for_email(report: Report, ai) -> list:
    """Unified list of (offer, tier, reason|None) — AI ranking if available,
    else the deterministic value ranking."""
    if ai is not None and ai.picks:
        return ai.picks
    return [(o, _deterministic_tier(o), None) for o in report.picks]


def render_text(report: Report, alert_keys: set, ts: str, ai=None) -> str:
    L: list[str] = []
    new_anoms = [a for a in report.anomalies if anom_key(a) in alert_keys]
    all_anoms = report.anomalies
    picks = picks_for_email(report, ai)

    L.append("=" * 64)
    L.append(f"REFURBED MACBOOK MONITOR — {ts}")
    if ai is not None and ai.summary:
        L.append("")
        L.append(_wrap(ai.summary))
    L.append("=" * 64)

    # ---- NOVO (the actual reason for this email) -------------------------- #
    fresh = new_offers(report, alert_keys)
    if fresh:
        L.append("")
        L.append(f"🆕 NOVO / JEFTINIJE OD ZADNJE PROVJERE ({len(fresh)})")
        L.append("-" * 64)
        for o in fresh[:8]:
            disc = f" −{o.discount_pct:.0f}%" if o.discount_pct else ""
            L.append(f"  • {o.price:.0f} €{disc}  {o.model} "
                     f"[{o.ram}/{o.storage} {o.color} {o.condition} {o.keyboard or '?'}]")
            note = _baseline_note(o)
            if note:
                L.append(f"      ↘ {note}")
            L.append(f"      {o.url}")

    # ---- TOP PONUDE (ranked) --------------------------------------------- #
    L.append("")
    src = "AI rangirano" if (ai and ai.picks) else "rangirano po vrijednosti"
    L.append(f"⭐ TOP PONUDE ({src}) — počni odavde")
    L.append("-" * 64)
    if picks:
        for o, tier, reason in picks:
            emoji = _TIER_EMOJI.get(tier, "•")
            nov = "  🆕" if offer_key(o) in alert_keys else ""
            disc = f" −{o.discount_pct:.0f}%" if o.discount_pct else ""
            L.append(f"  {emoji} {tier:<5} {o.price:>7.2f} €{disc}  {o.model} "
                     f"[{o.ram}/{o.storage} {o.color} {o.condition} {o.keyboard or '?'}]{nov}")
            if reason:
                L.append(f"        „{reason}”")
            note = _baseline_note(o)
            if note:
                L.append(f"        ↘ {note}")
            L.append(f"        {o.url}")
    else:
        L.append("  (nema ponude koja zadovoljava budžet + specifikacije)")

    # ---- ISPOD PROSJEKA --------------------------------------------------- #
    L.append("")
    L.append("🎯 ISPOD PROSJEKA — jeftinije nego inače za tu konfiguraciju")
    L.append("-" * 64)
    if report.underpriced:
        for o in report.underpriced[:8]:
            atl = " 🏆" if o.all_time_low else ""
            mark = "  ⭐NOVO" if offer_key(o) in alert_keys else ""
            L.append(f"  • {o.price:.0f} € (inače ~{o.baseline_median:.0f} €, "
                     f"−{o.vs_baseline_pct:.0f}%){atl}  {o.model} "
                     f"[{o.ram}/{o.storage} {o.color} {o.condition} "
                     f"{o.keyboard or '?'}]{mark}")
            L.append(f"      {o.url}")
    else:
        L.append("  (još skupljam prosjeke — bit će bogatije za koji dan)")

    # ---- ANOMALIJE -------------------------------------------------------- #
    L.append("")
    L.append(f"🔧 ANOMALIJE — skoro besplatni upgradei "
             f"({len(all_anoms)} ukupno, {len(new_anoms)} novih)")
    L.append("-" * 64)
    if all_anoms:
        for a in all_anoms[:12]:
            tag = "NOVO " if anom_key(a) in alert_keys else "     "
            L.append(f"  {tag}[{a.kind}] {a.text}")
            L.append(f"        ↳ {a.upgrade.url}")
        if len(all_anoms) > 12:
            L.append(f"  … +{len(all_anoms) - 12} dodatnih anomalija")
    else:
        L.append("  (nema)")

    # ---- VRIJEDNO SPOMENA ------------------------------------------------- #
    L.append("")
    L.append("💶 VRIJEDNO SPOMENA")
    L.append("-" * 64)
    L.append(f"  Najjeftinije pod stropom ({config.CEILING:.0f} €), "
             f"≥{config.GOOD_RAM}GB RAM / ≥{config.GOOD_STORAGE}GB:")
    if report.deals:
        for o in report.deals[: config.TOP_ABSOLUTE_DEALS]:
            mark = "  ⭐NOVO" if offer_key(o) in alert_keys else ""
            L.append(_fmt_offer_line(o) + mark)
    else:
        L.append("  (nema ponuda pod stropom)")

    # ---- SAŽETAK ---------------------------------------------------------- #
    L.append("")
    L.append("📊 SAŽETAK")
    L.append("-" * 64)
    for s in report.summaries:
        L.append(f"  • {s.model}: {s.config_count} configa, "
                 f"{s.worth_count} vrijednih, {s.anomaly_count} anomalija")
    total_av = sum(1 for o in report.offers if o.available)
    L.append("")
    L.append(f"  Ukupno: {len(report.offers)} configa "
             f"({total_av} dostupnih) • {len(all_anoms)} anomalija • "
             f"{len(alert_keys)} novih/jeftinijih")
    L.append("=" * 64)
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# HTML rendering — a comparison table (what Gmail shows)
# --------------------------------------------------------------------------- #
_TIER_HTML = {
    "STEAL": ("#ffffff", "#e8500e", "🔥"),
    "GREAT": ("#ffffff", "#1a8f3c", "🟢"),
    "GOOD": ("#333333", "#e3e3e8", "⚪"),
}


def _esc(x) -> str:
    return ihtml.escape(str(x if x is not None else ""))


def _anom_gain(a) -> str:
    if a.kind == "RAM":
        return f"{a.base.ram}→{a.upgrade.ram} GB RAM"
    if a.kind == "POHRANA":
        return f"{a.base.storage}→{a.upgrade.storage} GB pohrane"
    if a.kind == "BATERIJA":
        return "Optimalna→Nova baterija"
    return a.kind


def _vs_typical_html(o: Offer) -> str:
    if not o.baseline_median or o.vs_baseline_pct < 1:
        return "🏆" if o.all_time_low else "—"
    atl = "&nbsp;🏆" if o.all_time_low else ""
    return (f'<span style="color:#e8500e;font-weight:700" '
            f'title="inače ~{o.baseline_median:.0f} €">'
            f'−{o.vs_baseline_pct:.0f}%{atl}</span>')


def _th(cells: list[str]) -> str:
    return ('<tr style="background:#f5f5f7;text-align:left;color:#555">'
            + "".join(f'<th style="padding:7px 9px;font-weight:600">{c}</th>'
                      for c in cells) + "</tr>")


def _cell(content, extra="") -> str:
    return (f'<td style="padding:7px 9px;border-bottom:1px solid #eee;'
            f'vertical-align:middle;{extra}">{content}</td>')


def render_html(report: Report, alert_keys: set, ts: str, ai=None) -> str:
    picks = picks_for_email(report, ai)
    o_: list[str] = []
    o_.append('<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,'
              'Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;max-width:880px;'
              'margin:0 auto">')
    o_.append(f'<div style="color:#999;font-size:12px;margin-bottom:4px">refurbed '
              f'monitor · {_esc(ts)}</div>')
    if ai is not None and ai.summary:
        o_.append(f'<p style="font-size:15px;line-height:1.55;margin:6px 0 14px">'
                  f'{_esc(ai.summary)}</p>')

    # ----- NOVO table (the reason for this email) -------------------------- #
    fresh = new_offers(report, alert_keys)
    if fresh:
        o_.append('<h2 style="margin:14px 0 8px;font-size:18px">🆕 Novo / jeftinije '
                  '<span style="font-weight:400;font-size:13px;color:#999">'
                  '(od zadnje provjere)</span></h2>')
        o_.append('<table style="border-collapse:collapse;width:100%;font-size:13px">')
        o_.append(_th(["Cijena", "Popust", "vs tip.", "Model", "RAM/SSD",
                       "Stanje", "Tipk.", ""]))
        for o in fresh[:8]:
            disc = (f'<span style="color:#1a8f3c;font-weight:700">'
                    f'−{o.discount_pct:.0f}%</span>' if o.discount_pct else "—")
            buy = (f'<a href="{_esc(o.url)}" style="color:#fff;background:#5b51d8;'
                   f'padding:5px 11px;border-radius:6px;text-decoration:none;'
                   f'font-weight:600">Kupi&nbsp;›</a>')
            cells = [
                _cell(f'<b style="font-size:14px">{o.price:.0f}&nbsp;€</b>'),
                _cell(disc), _cell(_vs_typical_html(o)), _cell(_esc(o.model)),
                _cell(f'{o.ram}/{o.storage}'), _cell(_esc(o.condition)),
                _cell(_esc(o.keyboard or "?")), _cell(buy),
            ]
            o_.append('<tr style="background:#fffbe6">' + "".join(cells) + "</tr>")
        o_.append("</table>")

    # ----- TOP PONUDE table ------------------------------------------------ #
    src = "AI rangirano" if (ai and ai.picks) else "rangirano po vrijednosti"
    o_.append(f'<h2 style="margin:14px 0 8px;font-size:18px">⭐ TOP ponude '
              f'<span style="font-weight:400;font-size:13px;color:#999">({src})'
              f'</span></h2>')
    if picks:
        o_.append('<table style="border-collapse:collapse;width:100%;font-size:13px">')
        o_.append(_th(["#", "Tier", "Cijena", "Popust", "vs tip.", "Model",
                       "RAM/SSD", "Stanje", "Tipk.", "Bat.", "Zašto", ""]))
        for i, (o, tier, reason) in enumerate(picks, 1):
            fg, bg, emoji = _TIER_HTML.get(tier, ("#333", "#e3e3e8", "•"))
            new = offer_key(o) in alert_keys
            rowbg = "background:#fffbe6;" if new else ""
            badge = (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                     f'border-radius:11px;font-size:11px;font-weight:700;'
                     f'white-space:nowrap">{emoji} {tier}</span>')
            disc = (f'<span style="color:#1a8f3c;font-weight:700">'
                    f'−{o.discount_pct:.0f}%</span>' if o.discount_pct else "—")
            batt = "Nova" if o.battery == "new" else "Opt."
            buy = (f'<a href="{_esc(o.url)}" style="color:#fff;background:#5b51d8;'
                   f'padding:5px 11px;border-radius:6px;text-decoration:none;'
                   f'white-space:nowrap;font-weight:600">Kupi&nbsp;›</a>')
            cells = [
                _cell(f'{i}{"&nbsp;🆕" if new else ""}'),
                _cell(badge),
                _cell(f'<b style="font-size:14px">{o.price:.0f}&nbsp;€</b>'),
                _cell(disc),
                _cell(_vs_typical_html(o)),
                _cell(_esc(o.model)),
                _cell(f'{o.ram}/{o.storage}'),
                _cell(_esc(o.condition)),
                _cell(_esc(o.keyboard or "?")),
                _cell(batt),
                _cell(f'<span style="color:#555">{_esc(reason)}</span>'
                      if reason else "—"),
                _cell(buy),
            ]
            o_.append(f'<tr style="{rowbg}">' + "".join(cells) + "</tr>")
        o_.append("</table>")
    else:
        o_.append('<p style="color:#999">Nema ponude koja zadovoljava budžet + '
                  'specifikacije.</p>')

    # ----- Anomalije table ------------------------------------------------- #
    if report.anomalies:
        new_n = sum(1 for a in report.anomalies if anom_key(a) in alert_keys)
        o_.append(f'<h3 style="margin:22px 0 8px;font-size:16px">🔧 Anomalije '
                  f'<span style="font-weight:400;font-size:12px;color:#999">'
                  f'(skoro besplatni upgradei · {len(report.anomalies)} ukupno, '
                  f'{new_n} novih)</span></h3>')
        o_.append('<table style="border-collapse:collapse;width:100%;font-size:13px">')
        o_.append(_th(["Što dobiješ", "Stroj", "Trošak", "Cijena", ""]))
        for a in report.anomalies[:14]:
            u = a.upgrade
            new = anom_key(a) in alert_keys
            rowbg = "background:#fffbe6;" if new else ""
            color = "#1a8f3c" if a.delta <= 0 else "#1a1a1a"
            cost = (f'<span style="color:{color};font-weight:700">'
                    f'{a.delta:+.0f}&nbsp;€</span>')
            buy = (f'<a href="{_esc(u.url)}" style="color:#5b51d8;'
                   f'text-decoration:none;font-weight:600">otvori&nbsp;›</a>')
            cells = [
                _cell(f'{"🆕&nbsp;" if new else ""}<b>{_esc(_anom_gain(a))}</b>'),
                _cell(_esc(f"{u.model} · {u.color} · {u.condition} · {u.keyboard or '?'}")),
                _cell(cost),
                _cell(f'{u.price:.0f}&nbsp;€'),
                _cell(buy),
            ]
            o_.append(f'<tr style="{rowbg}">' + "".join(cells) + "</tr>")
        o_.append("</table>")

    # ----- Ispod prosjeka table -------------------------------------------- #
    if report.underpriced:
        o_.append('<h3 style="margin:22px 0 8px;font-size:16px">🎯 Ispod prosjeka '
                  '<span style="font-weight:400;font-size:12px;color:#999">'
                  '(jeftinije nego inače za tu konfiguraciju)</span></h3>')
        o_.append('<table style="border-collapse:collapse;width:100%;font-size:13px">')
        o_.append(_th(["Cijena", "Inače", "Razlika", "Model", "RAM/SSD",
                       "Stanje", ""]))
        for o in report.underpriced[:10]:
            new = offer_key(o) in alert_keys
            rowbg = "background:#fffbe6;" if new else ""
            atl = "&nbsp;🏆" if o.all_time_low else ""
            buy = (f'<a href="{_esc(o.url)}" style="color:#5b51d8;'
                   f'text-decoration:none;font-weight:600">otvori&nbsp;›</a>')
            cells = [
                _cell(f'<b style="font-size:14px">{o.price:.0f}&nbsp;€</b>{atl}'),
                _cell(f'<span style="color:#999">~{o.baseline_median:.0f}&nbsp;€</span>'),
                _cell(f'<span style="color:#e8500e;font-weight:700">'
                      f'−{o.vs_baseline_pct:.0f}%</span>'),
                _cell(_esc(o.model)),
                _cell(f'{o.ram}/{o.storage}'),
                _cell(_esc(o.condition)),
                _cell(buy),
            ]
            o_.append(f'<tr style="{rowbg}">' + "".join(cells) + "</tr>")
        o_.append("</table>")

    total_av = sum(1 for o in report.offers if o.available)
    o_.append(f'<p style="color:#999;font-size:12px;margin-top:18px">'
              f'{len(report.offers)} konfiguracija ({total_av} dostupnih) · '
              f'{len(report.anomalies)} anomalija · {len(alert_keys)} novih/jeftinijih '
              f'· US/HR tipkovnica · ≤ {config.CEILING:.0f} €</p>')
    o_.append("</div>")
    return "\n".join(o_)


def subject_line(report: Report, alert_keys: set, ai=None) -> str:
    if ai is not None and ai.subject:
        return ai.subject
    top = report.picks[0] if report.picks else None
    if top is not None:
        disc = f", −{top.discount_pct:.0f}%" if top.discount_pct else ""
        return (f"🟢 Refurbed: {len(alert_keys)} novih • top {top.price:.0f}€{disc} "
                f"{top.model}")
    return f"🟢 Refurbed: {len(alert_keys)} novih"


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def _smtp_config() -> Optional[tuple]:
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("ALERT_TO") or user
    if user and pwd and to:
        host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("SMTP_PORT", "465"))
        return host, port, user, pwd, to
    return None


def send_email(subject: str, body: str, html_body: Optional[str] = None) -> bool:
    cfg = _smtp_config()
    if not cfg:
        print("  [email] SMTP_USER/SMTP_PASS/ALERT_TO not set — skipping send.")
        return False
    host, port, user, pwd, to = cfg
    if html_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))        # fallback first
        msg.attach(MIMEText(html_body, "html", "utf-8"))    # preferred
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as srv:
            srv.login(user, pwd)
            srv.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
        print(f"  [email] sent to {to}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [email] FAILED: {exc}")
        return False


def send_telegram(subject: str, body: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    text = f"*{subject}*\n```\n{body}\n```"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text[:4000], "parse_mode": "Markdown"},
            timeout=20,
        )
        ok = r.status_code == 200
        print(f"  [telegram] {'sent' if ok else 'failed: ' + r.text[:120]}")
        return ok
    except requests.RequestException as exc:
        print(f"  [telegram] FAILED: {exc}")
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
