"""
State (seen.json dedup) + alert rendering + delivery (SMTP / Telegram).

Signatures (brief §7):
  DEAL|<product>|<spec>|<price>
  ANOM|<product>|...        (Anomaly.signature)
  PATH|<product>|<spec>|<price>

Only NEW signatures trigger an email. A run with no new signatures sends nothing
(it just logs), satisfying "a second run with no market change sends no email".
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

import requests

from . import config
from .analyze import Anomaly, Offer, Report

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seen.json")
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
    # prune entries older than SEEN_TTL_DAYS
    now = datetime.now(timezone.utc)
    pruned = {}
    for sig, iso in seen.items():
        try:
            age = (now - datetime.fromisoformat(iso)).days
        except (ValueError, TypeError):
            age = 0
        if age <= SEEN_TTL_DAYS:
            pruned[sig] = iso
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, ensure_ascii=False, indent=1, sort_keys=True)


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #
def _deal_sig(o: Offer) -> str:
    return f"DEAL|{o.product}|{o.ram}/{o.storage}|{o.color}|{o.condition}|{o.price:.2f}"


def _path_sig(spec: tuple, o: Offer) -> str:
    return f"PATH|{o.product}|{spec[0]}/{spec[1]}|{o.color}|{o.condition}|{o.price:.2f}"


def _pick_sig(o: Offer) -> str:
    return f"PICK|{o.product}|{o.ram}/{o.storage}|{o.color}|{o.condition}|{o.price:.2f}"


def current_signatures(report: Report) -> dict:
    """All signatures present in this run, mapped to a short human label."""
    sigs: dict[str, str] = {}
    for o in report.picks:
        sigs[_pick_sig(o)] = f"PICK {o.model} {o.spec_label} — {o.price:.2f} €"
    for o in report.deals:
        sigs[_deal_sig(o)] = f"DEAL {o.model} {o.spec_label} — {o.price:.2f} €"
    for spec, o in report.paths.items():
        if o is not None:
            sigs[_path_sig(spec, o)] = (
                f"PATH {spec[0]}/{spec[1]} → {o.model} {o.spec_label} — {o.price:.2f} €"
            )
    for a in report.anomalies:
        sigs[a.signature] = f"ANOM {a.text}"
    return sigs


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _wrap(text: str, width: int = 64) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width)) or text


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


def render_text(report: Report, new_sigs: set, ts: str, ai=None) -> str:
    L: list[str] = []
    new_anoms = [a for a in report.anomalies if a.signature in new_sigs]
    all_anoms = report.anomalies
    picks = picks_for_email(report, ai)

    L.append("=" * 64)
    L.append(f"REFURBED MACBOOK MONITOR — {ts}")
    if ai is not None and ai.summary:
        L.append("")
        L.append(_wrap(ai.summary))
    L.append("=" * 64)

    # ---- TOP PONUDE (ranked) --------------------------------------------- #
    L.append("")
    src = "AI rangirano" if (ai and ai.picks) else "rangirano po vrijednosti"
    L.append(f"⭐ TOP PONUDE ({src}) — počni odavde")
    L.append("-" * 64)
    if picks:
        for o, tier, reason in picks:
            emoji = _TIER_EMOJI.get(tier, "•")
            nov = "  🆕" if _pick_sig(o) in new_sigs or _deal_sig(o) in new_sigs else ""
            disc = f" −{o.discount_pct:.0f}%" if o.discount_pct else ""
            L.append(f"  {emoji} {tier:<5} {o.price:>7.2f} €{disc}  {o.model} "
                     f"[{o.ram}/{o.storage} {o.color} {o.condition} {o.keyboard or '?'}]{nov}")
            if reason:
                L.append(f"        „{reason}”")
            L.append(f"        {o.url}")
    else:
        L.append("  (nema ponude koja zadovoljava budžet + specifikacije)")

    # ---- ANOMALIJE -------------------------------------------------------- #
    L.append("")
    L.append(f"🔧 ANOMALIJE — skoro besplatni upgradei "
             f"({len(all_anoms)} ukupno, {len(new_anoms)} novih)")
    L.append("-" * 64)
    if all_anoms:
        for a in all_anoms[:12]:
            tag = "NOVO " if a.signature in new_sigs else "     "
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
            mark = "  ⭐NOVO" if _deal_sig(o) in new_sigs else ""
            L.append(_fmt_offer_line(o) + mark)
    else:
        L.append("  (nema ponuda pod stropom)")

    L.append("")
    L.append("  Najjeftiniji put do ciljanih specifikacija:")
    for spec, o in report.paths.items():
        label = f"{spec[0]}GB / {spec[1]}GB"
        if o is None:
            L.append(f"  • {label}: (nema dostupno)")
        else:
            mark = "  ⭐NOVO" if _path_sig(spec, o) in new_sigs else ""
            got = f"{o.ram}/{o.storage}"
            extra = "  ⬆" if (o.ram > spec[0] or o.storage > spec[1]) else ""
            L.append(f"  • {label}: {o.price:.2f} € — {o.model} "
                     f"[{got} {o.color} {o.condition}{extra}]{mark}")
            L.append(f"      {o.url}")

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
             f"{len(new_sigs)} novih signala")
    L.append("=" * 64)
    return "\n".join(L)


def subject_line(report: Report, new_sigs: set, ai=None) -> str:
    if ai is not None and ai.subject:
        return ai.subject
    top = report.picks[0] if report.picks else None
    if top is not None:
        disc = f", −{top.discount_pct:.0f}%" if top.discount_pct else ""
        return (f"🟢 Refurbed: {len(new_sigs)} novih • top {top.price:.0f}€{disc} "
                f"{top.model}")
    return f"🟢 Refurbed: {len(new_sigs)} novih"


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


def send_email(subject: str, body: str) -> bool:
    cfg = _smtp_config()
    if not cfg:
        print("  [email] SMTP_USER/SMTP_PASS/ALERT_TO not set — skipping send.")
        return False
    host, port, user, pwd, to = cfg
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
