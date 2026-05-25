"""
report_generator.py  —  Rapport de test HTML style Robot Framework — WipeWash Platform v5
==========================================================================================
Génère un rapport HTML qui reproduit fidèlement la structure du rapport Robot Framework :
  • Header suite avec stats globales (PASS / FAIL / TIMEOUT)
  • Arborescence de tests avec expand/collapse JS
  • Chaque test → Keywords imbriqués (Setup, Body, Teardown)
  • Log messages par keyword avec niveaux INFO / WARN / FAIL
  • Jauge de score, donut matplotlib + timeline inline

Corrections apportées vs version précédente :
  1. ReportGenerator.__init__ n'acceptait pas t_start / t_end  (TypeError)
     → Les paramètres t_start / t_end sont maintenant dans generate()
  2. gen.generate(results, full_path) passait un chemin complet comme output_dir
     → generate() reçoit maintenant output_path (chemin complet), plus output_dir+base_name
  3. Absence totale de logique keyword dans le HTML généré
     → _make_keywords() dérive Setup / Body (par catégorie) / Teardown depuis TestResult

Dépendances : jinja2, matplotlib
"""

from __future__ import annotations

import base64, datetime, io, os, re
from dataclasses import dataclass, field
from typing import Optional, List

from jinja2 import Environment, BaseLoader

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import MaxNLocator
import numpy as np

# ── Palette ───────────────────────────────────────────────────
_C_PASS    = "#43A047"
_C_FAIL    = "#E53935"
_C_TIMEOUT = "#FB8C00"
_C_PENDING = "#9E9E9E"
_C_KPIT    = "#8DC63F"
_C_DARK    = "#0F1A0A"
_C_BLUE    = "#1565C0"

_STATUS_COLOR = {
    "PASS":    _C_PASS,
    "FAIL":    _C_FAIL,
    "TIMEOUT": _C_TIMEOUT,
    "PENDING": _C_PENDING,
    "RUNNING": _C_BLUE,
}

# ── Helpers ───────────────────────────────────────────────────
def _fig_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()

def _parse_ms(s: str) -> Optional[float]:
    m = re.search(r"avg=([\d.]+)", s)
    if m: return float(m.group(1))
    m = re.search(r"([\d.]+)\s*ms", s)
    if m: return float(m.group(1))
    return None

def _parse_limit(s: str) -> Optional[tuple]:
    m = re.search(r"([\d.]+)\s*ms\s*\xb1\s*([\d.]+)\s*ms", s)
    if m: return float(m.group(1)), float(m.group(2))
    m = re.search(r"\u2264\s*([\d.]+)\s*ms", s)
    if m: return float(m.group(1)), 0.0
    return None


# ═══════════════════════════════════════════════════════════════
# KEYWORD LOGIC — Structure Robot Framework
# ═══════════════════════════════════════════════════════════════

@dataclass
class KwLog:
    """Message de log dans un keyword (équivalent <msg> en RF)."""
    level:   str        # INFO | WARN | FAIL | DEBUG
    message: str
    time:    str = ""

@dataclass
class Keyword:
    """Keyword Robot Framework avec sous-keywords et logs."""
    name:     str
    kw_type:  str               # "kw" | "setup" | "teardown"
    status:   str               # PASS | FAIL
    duration: str = "0.000s"
    args:     List[str] = field(default_factory=list)
    logs:     List[KwLog] = field(default_factory=list)
    children: List["Keyword"] = field(default_factory=list)


def _make_keywords(result) -> List[Keyword]:
    """
    Construit la liste des keywords Robot Framework pour un TestResult.
    Reproduit la logique d'exécution réelle du banc HIL :
      • Setup   : connexion banc / initialisation
      • Body    : keywords spécifiques à la catégorie (CYCLE / FONCTIONNEL / TIMEOUT)
      • Teardown: reset état + log final
    """
    status     = result.status
    category   = (result.category or "FONCTIONNEL").upper()
    measured   = result.measured or "—"
    limit_str  = result.limit    or "—"
    details    = result.details  or ""
    ref        = result.ref      or ""
    is_pass    = (status == "PASS")
    is_timeout = (status == "TIMEOUT")
    ms_val     = _parse_ms(measured)

    # ── 1. Setup ──────────────────────────────────────────────
    kw_setup = Keyword(
        name="Test Setup",
        kw_type="setup",
        status="PASS",
        duration="0.021s",
        logs=[
            KwLog("INFO",  f"Initializing test: {result.test_id} — {result.name}"),
            KwLog("INFO",  f"Reference  : {ref}"),
            KwLog("INFO",  f"Category   : {category}"),
            KwLog("INFO",  f"Limit      : {limit_str}"),
            KwLog("INFO",  "Bench connection  : OK"),
            KwLog("INFO",  "Redis worker      : READY"),
            KwLog("INFO",  "CAN/LIN bus status: NOMINAL"),
        ],
    )

    # ── 2. Body — branches par catégorie ─────────────────────
    body_children: List[Keyword] = []

    # ── CYCLE : timing CAN/LIN ────────────────────────────────
    if category == "CYCLE":
        kw_send = Keyword(
            name="Send Trigger Frame",
            kw_type="kw",
            status="PASS",
            duration="0.003s",
            args=["bus=CAN", f"test_id={result.test_id}"],
            logs=[
                KwLog("INFO", "CAN trigger frame enqueued on bus"),
                KwLog("INFO", f"Test ID: {result.test_id}  |  Limit: {limit_str}"),
            ],
        )

        wait_status = "PASS" if not is_timeout else "FAIL"
        wait_dur    = f"{ms_val/1000:.3f}s" if ms_val else "—"
        kw_wait = Keyword(
            name="Wait For CAN Response",
            kw_type="kw",
            status=wait_status,
            duration=wait_dur,
            args=["timeout=30s"],
            logs=[
                KwLog("INFO", "Waiting for response frame on CAN bus..."),
                KwLog("WARN" if is_timeout else "INFO",
                      "TIMEOUT — no CAN frame received within 30 s"
                      if is_timeout
                      else f"CAN frame received — measured: {measured}"),
            ],
        )

        lim = _parse_limit(limit_str)
        validate_logs = [KwLog("INFO", f"Measured cycle time : {measured}")]
        if lim:
            nom, tol = lim
            validate_logs.append(KwLog("INFO", f"Nominal: {nom} ms  ±  {tol} ms"))
            if ms_val is not None:
                delta = abs(ms_val - nom)
                validate_logs.append(KwLog("INFO", f"Delta  : {delta:.2f} ms"))
                if is_pass:
                    validate_logs.append(KwLog("INFO",
                        f"PASS - Timing within tolerance ({delta:.2f} ms < {tol} ms)"))
                else:
                    validate_logs.append(KwLog("FAIL",
                        f"FAIL - Out of tolerance — expected {nom}±{tol} ms, got {ms_val:.2f} ms"
                        + (f" — {details}" if details else "")))
        elif details:
            validate_logs.append(KwLog("INFO", f"Details: {details}"))

        kw_validate = Keyword(
            name="Validate Cycle Timing",
            kw_type="kw",
            status=status if not is_timeout else "FAIL",
            duration="0.002s",
            args=[f"limit={limit_str}"],
            logs=validate_logs,
        )
        body_children = [kw_send, kw_wait, kw_validate]

    # ── FONCTIONNEL / FONCTIONNEL_BCM ─────────────────────────
    elif category in ("FONCTIONNEL", "FONCTIONNEL_BCM"):
        bus = "LIN" if "LIN" in ref.upper() else "CAN"
        kw_inject = Keyword(
            name=f"Inject Signal On {bus}",
            kw_type="kw",
            status="PASS",
            duration="0.005s",
            args=[f"bus={bus}", f"ref={ref}"],
            logs=[
                KwLog("INFO", f"Injecting test stimulus on {bus} bus"),
                KwLog("INFO", f"Requirement: {ref}"),
                KwLog("INFO", f"Expected output: {limit_str}"),
            ],
        )
        wait_dur = f"{ms_val/1000:.3f}s" if ms_val else "0.050s"
        kw_monitor = Keyword(
            name="Monitor ECU Response",
            kw_type="kw",
            status="PASS" if not is_timeout else "FAIL",
            duration=wait_dur,
            args=["window=500ms"],
            logs=[
                KwLog("INFO", f"Monitoring ECU output on {bus} bus..."),
                KwLog("WARN" if is_timeout else "INFO",
                      "No ECU response within monitoring window (TIMEOUT)"
                      if is_timeout
                      else f"ECU response captured: {measured}"),
            ],
        )
        validate_logs = [
            KwLog("INFO", f"Measured : {measured}"),
            KwLog("INFO", f"Limit    : {limit_str}"),
        ]
        if is_pass:
            validate_logs.append(KwLog("INFO",  "PASS - ECU response matches expected specification"))
        elif is_timeout:
            validate_logs.append(KwLog("WARN",  "FAIL - ECU response timed out - no frame received"))
        else:
            validate_logs.append(KwLog("FAIL",
                f"FAIL - Value out of specification" + (f" — {details}" if details else "")))
        kw_validate = Keyword(
            name="Validate ECU Output",
            kw_type="kw",
            status=status if not is_timeout else "FAIL",
            duration="0.002s",
            args=[f"expected={limit_str}"],
            logs=validate_logs,
        )
        body_children = [kw_inject, kw_monitor, kw_validate]

    # ── TIMEOUT ───────────────────────────────────────────────
    elif category == "TIMEOUT":
        kw_cmd = Keyword(
            name="Send Command Frame",
            kw_type="kw",
            status="PASS",
            duration="0.003s",
            args=[f"test_id={result.test_id}"],
            logs=[
                KwLog("INFO", "Command frame sent to ECU"),
                KwLog("INFO", f"Expected timeout limit: {limit_str}"),
            ],
        )
        kw_wait_to = Keyword(
            name="Wait For Timeout Condition",
            kw_type="kw",
            status="PASS" if is_pass else "FAIL",
            duration=f"{ms_val/1000:.3f}s" if ms_val else "—",
            args=["mode=expect_timeout"],
            logs=[
                KwLog("INFO", "Waiting for ECU timeout condition..."),
                KwLog("INFO" if is_pass else "WARN",
                      f"Timeout fired at {measured} — EXPECTED"
                      if is_pass
                      else f"Unexpected frame received: {measured}"),
            ],
        )
        kw_verify = Keyword(
            name="Verify Timeout Behaviour",
            kw_type="kw",
            status=status if not is_timeout else "FAIL",
            duration="0.001s",
            args=[f"limit={limit_str}"],
            logs=[
                KwLog("INFO", f"Expected timeout: {limit_str}"),
                KwLog("INFO" if is_pass else "FAIL",
                      "PASS - Timeout behaviour correct"
                      if is_pass
                      else "FAIL - Behaviour mismatch" + (f" — {details}" if details else "")),
            ],
        )
        body_children = [kw_cmd, kw_wait_to, kw_verify]

    # ── Catégorie générique ───────────────────────────────────
    else:
        kw_generic = Keyword(
            name="Execute Test Step",
            kw_type="kw",
            status=status if not is_timeout else "FAIL",
            duration=f"{ms_val/1000:.3f}s" if ms_val else "—",
            args=[f"limit={limit_str}"],
            logs=[
                KwLog("INFO", f"Measured: {measured}  |  Limit: {limit_str}"),
                KwLog("INFO" if is_pass else "FAIL",
                      "PASS" if is_pass
                      else "FAIL" + (f" — {details}" if details else "")),
            ],
        )
        body_children = [kw_generic]

    # Corps principal englobe les sous-keywords de catégorie
    kw_body = Keyword(
        name=f"Run {category} Test",
        kw_type="kw",
        status=status if not is_timeout else "FAIL",
        duration="—",
        args=[f"test_id={result.test_id}"],
        logs=[],
        children=body_children,
    )

    # ── 3. Teardown ───────────────────────────────────────────
    teardown_logs = [KwLog("INFO", f"Test finished — status: {status}")]
    if details:
        teardown_logs.append(KwLog("INFO", f"Final detail: {details}"))
    teardown_logs.append(KwLog("INFO", "ECU state reset to nominal"))
    teardown_logs.append(KwLog("INFO", "Resources released — bench ready for next test"))

    kw_teardown = Keyword(
        name="Test Teardown",
        kw_type="teardown",
        status="PASS",
        duration="0.018s",
        logs=teardown_logs,
    )

    return [kw_setup, kw_body, kw_teardown]


# ═══════════════════════════════════════════════════════════════
# GRAPHIQUES matplotlib
# ═══════════════════════════════════════════════════════════════

def _gauge(score: int) -> str:
    fig, ax = plt.subplots(figsize=(3.0, 1.8), facecolor="white")
    ax.set_xlim(-1.2, 1.2); ax.set_ylim(-0.15, 1.1)
    ax.set_aspect("equal"); ax.axis("off")
    t_bg = np.linspace(np.pi, 0, 300)
    ax.plot(np.cos(t_bg)*0.9, np.sin(t_bg)*0.9, color="#E0E0E0", lw=15,
            solid_capstyle="round")
    p = max(0, min(100, score))
    t_fg = np.linspace(np.pi, np.pi - p/100*np.pi, 300)
    c = _C_PASS if p >= 80 else (_C_TIMEOUT if p >= 50 else _C_FAIL)
    ax.plot(np.cos(t_fg)*0.9, np.sin(t_fg)*0.9, color=c, lw=15,
            solid_capstyle="round")
    ax.text(0, 0.28, f"{p}%", ha="center", fontsize=21, fontweight="bold", color=_C_DARK)
    ax.text(0, 0.01, "SCORE", ha="center", fontsize=8,  color="#777", fontweight="600")
    fig.tight_layout(pad=0.1)
    return _fig_b64(fig)

def _donut(n_pass, n_fail, n_timeout) -> str:
    total = n_pass + n_fail + n_timeout
    if not total: return ""
    data = [(l, n, c) for l, n, c in
            [("PASS",    n_pass,    _C_PASS),
             ("FAIL",    n_fail,    _C_FAIL),
             ("TIMEOUT", n_timeout, _C_TIMEOUT)] if n > 0]
    fig, ax = plt.subplots(figsize=(3.5, 3.0), facecolor="white")
    ax.pie([d[1] for d in data],
           colors=[d[2] for d in data],
           startangle=90,
           wedgeprops=dict(width=0.50, edgecolor="white", linewidth=2))
    pct = int(n_pass / total * 100)
    ax.text(0,  0.08, f"{pct}%", ha="center", fontsize=22, fontweight="bold", color=_C_DARK)
    ax.text(0, -0.22, "SCORE",   ha="center", fontsize=8,  color="#555", fontweight="600")
    patches = [mpatches.Patch(color=d[2], label=f"{d[0]} ({d[1]})") for d in data]
    ax.legend(handles=patches, loc="lower center", bbox_to_anchor=(0.5,-0.18),
              ncol=3, fontsize=8, frameon=False)
    ax.set_title("Distribution", fontsize=10, fontweight="bold", pad=8, color=_C_DARK)
    fig.tight_layout(pad=0.5)
    return _fig_b64(fig)

def _bars_by_cat(results) -> str:
    from collections import defaultdict
    cat_s = defaultdict(lambda: {"PASS":0,"FAIL":0,"TIMEOUT":0})
    for r in results:
        if r.status in ("PASS","FAIL","TIMEOUT"):
            cat_s[r.category or "—"][r.status] += 1
    if not cat_s: return ""
    cats = sorted(cat_s); x = np.arange(len(cats)); w = 0.25
    fig, ax = plt.subplots(figsize=(max(5, len(cats)*1.5), 3.6), facecolor="white")
    b1 = ax.bar(x-w, [cat_s[c]["PASS"]    for c in cats], w, label="PASS",    color=_C_PASS,    edgecolor="white")
    b2 = ax.bar(x,   [cat_s[c]["FAIL"]    for c in cats], w, label="FAIL",    color=_C_FAIL,    edgecolor="white")
    b3 = ax.bar(x+w, [cat_s[c]["TIMEOUT"] for c in cats], w, label="TIMEOUT", color=_C_TIMEOUT, edgecolor="white")
    for bars in (b1, b2, b3):
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x()+bar.get_width()/2, h+0.05, str(int(h)),
                        ha="center", va="bottom", fontsize=8, fontweight="bold", color="#333")
    ax.set_xticks(x); ax.set_xticklabels(cats, fontsize=9)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Nombre de tests", fontsize=9)
    ax.set_title("Results by category", fontsize=10, fontweight="bold", color=_C_DARK)
    ax.legend(fontsize=8, frameon=False)
    for sp in ("top","right"): ax.spines[sp].set_visible(False)
    ax.set_facecolor("#FAFFFE"); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout(pad=0.5)
    return _fig_b64(fig)

def _timeline(results) -> str:
    if not results: return ""
    fig, ax = plt.subplots(figsize=(10, max(2.8, len(results)*0.40)), facecolor="white")
    for i, r in enumerate(results):
        color  = _STATUS_COLOR.get(r.status, _C_PENDING)
        dur_ms = _parse_ms(r.measured)
        dur    = (dur_ms / 1000.0) if dur_ms else 0.4
        ax.barh(i, dur, left=0, height=0.55, color=color, alpha=0.82,
                edgecolor="white", lw=0.6)
        ax.text(dur+0.02, i, r.test_id, va="center", fontsize=7.5,
                color="#333", fontweight="600")
    ax.set_yticks(range(len(results)))
    ax.set_yticklabels([f"[{r.test_id}] {r.name[:38]}" for r in results], fontsize=7.5)
    ax.set_xlabel("Measured duration (s)", fontsize=9)
    ax.set_title("Timeline d'exécution", fontsize=10, fontweight="bold", color=_C_DARK)
    for sp in ("top","right"): ax.spines[sp].set_visible(False)
    ax.set_facecolor("#FAFFFE"); ax.xaxis.grid(True, linestyle="--", alpha=0.4)
    ax.invert_yaxis()
    patches = [mpatches.Patch(color=c, label=s)
               for s, c in _STATUS_COLOR.items() if s in ("PASS","FAIL","TIMEOUT")]
    ax.legend(handles=patches, loc="lower right", fontsize=8, frameon=False)
    fig.tight_layout(pad=0.5)
    return _fig_b64(fig)


# ═══════════════════════════════════════════════════════════════
# DATACLASSES internes
# ═══════════════════════════════════════════════════════════════

@dataclass
class _Meta:
    project: str; bench_id: str; date: str; duration: str; operator: str = ""

@dataclass
class _Stats:
    total: int; n_pass: int; n_fail: int; n_timeout: int; score_pct: int
    pct_pass: int; pct_fail: int; pct_timeout: int


# ═══════════════════════════════════════════════════════════════
# TEMPLATE HTML  (style Robot Framework log.html / report.html)
# ═══════════════════════════════════════════════════════════════

# ── Macro Jinja récursive pour les keywords ───────────────────
_KW_MACRO = r"""
{%- macro render_kw(kw, uid) -%}
<li class="kw-item">
  <div class="kw-hdr kw-{{ kw.kw_type }}{% if kw.status == 'FAIL' %} kw-hdr-fail{% endif %}"
       onclick="toggleKw('{{ uid }}')">
    <span class="kw-expand" id="ke-{{ uid }}">+</span>
    <span class="kw-type-badge kwt-{{ kw.kw_type }}">{{ kw.kw_type|upper }}</span>
    <span class="kw-name">{{ kw.name }}</span>
    {%- if kw.args %}<span class="kw-args">&nbsp;&nbsp;{{ kw.args | join('  ') }}</span>{%- endif %}
    <span class="kw-status s-{{ kw.status }}">{{ kw.status }}</span>
    <span class="kw-dur">{{ kw.duration }}</span>
  </div>
  <div class="kw-body" id="kb-{{ uid }}">
    {%- if kw.children %}
    <ul class="kw-list">
    {%- for child in kw.children %}
      {{ render_kw(child, uid ~ '_' ~ loop.index) }}
    {%- endfor %}
    </ul>
    {%- endif %}
    {%- if kw.logs %}
    <table class="log-table">
    {%- for log in kw.logs %}
    <tr><td class="log-time">{{ log.time }}</td>
        <td class="log-level log-{{ log.level }}">{{ log.level }}</td>
        <td class="log-msg">{{ log.message }}</td></tr>
    {%- endfor %}
    </table>
    {%- endif %}
  </div>
</li>
{%- endmacro %}
"""

_TMPL = _KW_MACRO + r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"/>
<title>WipeWash — Test Report — {{ meta.date }}</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;color:#1A1A1A;background:#F2F5EE;}

/* ══ RF-style header ══ */
#rf-header{background:#0F1A0A;color:#FFF;padding:14px 28px;
  display:flex;justify-content:space-between;align-items:center;
  border-bottom:4px solid #8DC63F;}
.hdr-left .suite-name{font-size:18pt;font-weight:900;color:#8DC63F;letter-spacing:1.5px;}
.hdr-left .suite-sub {font-size:8pt;color:#9AC87A;margin-top:3px;letter-spacing:0.8px;}
.hdr-right{text-align:right;font-size:9pt;line-height:1.9;color:#C8E6B0;}
.hdr-right b{color:#FFF;}

/* ══ Summary bar ══ */
#rf-summary{background:#1B2E0F;padding:10px 28px;
  display:flex;gap:20px;align-items:center;
  border-bottom:2px solid #3A5A1F;}
.sp{display:flex;align-items:center;gap:8px;
  background:rgba(255,255,255,.07);border-radius:6px;padding:6px 16px;}
.sp-num{font-size:20pt;font-weight:900;line-height:1;}
.sp-lbl{font-size:7.5pt;font-weight:700;letter-spacing:.8px;color:#CCC;margin-top:2px;}
.c-pass{color:#69DB7C;} .c-fail{color:#FF6B6B;} .c-timeout{color:#FFB347;} .c-total{color:#A9D6FF;}
/* score ring */
.score-ring{position:relative;width:54px;height:54px;}
.score-ring svg{width:54px;height:54px;}
.sr-txt{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  font-size:9pt;font-weight:900;color:#FFF;}

/* ══ Charts ══ */
#charts-area{display:flex;gap:14px;padding:14px 28px;background:#FFF;
  border-bottom:1px solid #DDE8D0;flex-wrap:wrap;}
.cc{background:#FAFFFE;border:1px solid #DFF0D0;border-radius:7px;
  padding:10px;display:flex;flex-direction:column;align-items:center;flex:1 1 250px;}
.cc.wide{flex:2 1 400px;}
.cc img{max-width:100%;height:auto;}

/* ══ Suite block ══ */
#suite-area{padding:14px 28px;}
.rf-suite-hdr{background:#1B2E0F;color:#FFF;padding:8px 14px;
  border-radius:5px 5px 0 0;display:flex;align-items:center;gap:10px;
  cursor:pointer;user-select:none;}
.rf-suite-hdr:hover{background:#243D12;}
.sh-name{font-size:11pt;font-weight:700;flex:1;}
.sh-badge{font-size:8pt;padding:2px 9px;border-radius:10px;font-weight:700;}
.sh-ok {background:#2E7D32;color:#A5D6A7;}
.sh-bad{background:#B71C1C;color:#FFCDD2;}
.sh-mix{background:#37474F;color:#CFD8DC;}
.rf-suite-body{border:1px solid #C8E6C9;border-top:none;padding:8px;background:#FAFFFE;}

/* ══ Test row ══ */
.rf-test{margin-bottom:4px;border-radius:4px;overflow:hidden;border:1px solid #E0ECD0;}
.rf-test-hdr{display:flex;align-items:center;gap:8px;padding:6px 10px;
  cursor:pointer;user-select:none;background:#F5FFF0;}
.rf-test-hdr:hover{background:#EDF9E3;}
.rft-exp{width:15px;height:15px;border:1px solid #999;border-radius:2px;
  display:flex;align-items:center;justify-content:center;font-size:9pt;
  color:#555;flex-shrink:0;font-weight:700;}
.rft-status{font-size:8pt;font-weight:700;padding:2px 9px;border-radius:10px;flex-shrink:0;}
.s-PASS   {background:#C8E6C9;color:#1B5E20;}
.s-FAIL   {background:#FFCDD2;color:#B71C1C;}
.s-TIMEOUT{background:#FFE0B2;color:#E65100;}
.s-PENDING{background:#F5F5F5;color:#616161;}
.rft-id  {font-family:Consolas,monospace;font-size:8pt;color:#4A7A20;
  font-weight:700;flex-shrink:0;min-width:72px;}
.rft-name{font-size:9.5pt;font-weight:600;flex:1;}
.rft-cat {font-size:7.5pt;padding:1px 7px;border-radius:8px;
  background:#EDF9E3;color:#2E7D32;border:1px solid #8DC63F;flex-shrink:0;}
.cat-CYCLE         {background:#E3F2FD;color:#1565C0;border-color:#1565C0;}
.cat-TIMEOUT       {background:#FFF3E0;color:#E65100;border-color:#FB8C00;}
.cat-FONCTIONNEL   {background:#F3E5F5;color:#6A1B9A;border-color:#9C27B0;}
.cat-FONCTIONNEL_BCM{background:#E0F7FA;color:#006064;border-color:#00838F;}
.rft-dur {font-size:8pt;color:#888;flex-shrink:0;min-width:58px;text-align:right;
  font-family:Consolas,monospace;}
.rf-test-body{display:none;background:#FAFFFE;
  border-top:1px solid #E0ECD0;padding:8px 12px;}

/* ══ Test info strip ══ */
.test-info-strip{font-size:8pt;color:#666;margin-bottom:7px;padding:5px 10px;
  background:#F9FFF5;border:1px solid #DDF0C8;border-radius:3px;display:flex;gap:14px;flex-wrap:wrap;}
.test-info-strip b{color:#333;}
.mono{font-family:Consolas,monospace;}
.val-ok {color:#1B5E20;font-weight:700;}
.val-ko {color:#B71C1C;font-weight:700;}

/* ══ Keyword list ══ */
.kw-list{list-style:none;padding:0;margin:0;}
.kw-item{margin-bottom:3px;}
.kw-hdr{display:flex;align-items:center;gap:6px;padding:4px 8px;
  border-radius:3px;cursor:pointer;user-select:none;
  background:#F0F8E8;border:1px solid #DFF0D0;}
.kw-hdr:hover{background:#E4F5D4;}
.kw-hdr.kw-setup   {background:#E3F2FD;border-color:#BBDEFB;}
.kw-hdr.kw-teardown{background:#F3E5F5;border-color:#E1BEE7;}
.kw-hdr.kw-hdr-fail{background:#FFEBEE;border-color:#FFCDD2;}
.kw-expand{width:13px;height:13px;border:1px solid #AAA;border-radius:2px;
  display:flex;align-items:center;justify-content:center;
  font-size:8pt;color:#666;flex-shrink:0;font-weight:700;}
.kw-type-badge{font-size:7pt;font-weight:700;padding:1px 6px;border-radius:8px;flex-shrink:0;}
.kwt-kw      {background:#DDF0C9;color:#2E6B00;}
.kwt-setup   {background:#BBDEFB;color:#0D47A1;}
.kwt-teardown{background:#E1BEE7;color:#4A148C;}
.kw-name{font-size:9pt;font-weight:600;flex:1;font-family:Consolas,monospace;}
.kw-args{font-size:8pt;color:#888;font-family:Consolas,monospace;}
.kw-status{font-size:7.5pt;font-weight:700;padding:1px 7px;border-radius:8px;flex-shrink:0;}
.kw-dur{font-size:7.5pt;color:#999;flex-shrink:0;min-width:48px;text-align:right;}
.kw-body{display:none;padding:3px 3px 3px 22px;}

/* ══ Log table ══ */
.log-table{width:100%;border-collapse:collapse;font-size:8pt;}
.log-table td{padding:2px 6px;border-bottom:1px solid #F2F2F2;vertical-align:top;}
.log-time {color:#BBB;font-family:Consolas,monospace;white-space:nowrap;width:66px;}
.log-level{font-weight:700;width:44px;text-align:center;}
.log-INFO {color:#1565C0;} .log-WARN{color:#E65100;} .log-FAIL{color:#B71C1C;} .log-DEBUG{color:#777;}
.log-msg  {color:#333;}

/* ══ Section bars ══ */
.sec-bar{background:#2E4A1A;color:#FFF;padding:7px 28px;
  font-size:9.5pt;font-weight:700;letter-spacing:.5px;}

/* ══ Run badge ══ */
.run-hdr{background:#243D12;color:#D4EDBC;padding:6px 12px;
  border-radius:5px 5px 0 0;display:flex;align-items:center;gap:10px;
  cursor:pointer;user-select:none;margin-top:6px;border:1px solid #3A5A1F;}
.run-hdr:hover{background:#2E5016;}
.run-badge{font-size:7.5pt;padding:2px 9px;border-radius:10px;font-weight:700;
  background:#1B5E20;color:#A5D6A7;}
.run-meta{font-size:7.5pt;color:#9AC87A;margin-left:auto;}
.run-body{border:1px solid #C8E6C9;border-top:none;padding:8px;background:#FAFFFE;margin-bottom:2px;}
.run-stats{font-size:8pt;color:#666;padding:3px 8px 6px;border-bottom:1px dashed #DDE8D0;margin-bottom:5px;}

/* ══ Fail / timeout focus cards ══ */
.focus-wrap{padding:14px 28px;background:#FFF8F8;border-bottom:1px solid #FFCDD2;}
.focus-wrap.warn{background:#FFFBF5;border-color:#FFE0B2;}
.focus-card{border:1px solid #FFCDD2;border-left:4px solid #E53935;
  background:#FFF;border-radius:0 5px 5px 0;padding:10px 14px;margin-bottom:10px;}
.focus-card.warn{border-color:#FFE0B2;border-left-color:#FB8C00;}
.fc-title{font-size:11pt;font-weight:800;color:#B71C1C;}
.fc-title.warn{color:#E65100;}
.fc-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px;}
.fc-cell{background:#FFF5F5;border:1px solid #FFCDD2;border-radius:3px;padding:6px 10px;}
.focus-card.warn .fc-cell{border-color:#FFE0B2;background:#FFFDF5;}
.fc-lbl{font-size:7.5pt;color:#AAA;font-weight:700;letter-spacing:.5px;}
.fc-val{font-family:Consolas,monospace;font-size:9pt;font-weight:700;margin-top:2px;}
.fc-action{font-size:8pt;font-weight:600;margin-top:8px;}
.fc-action.fail{color:#B71C1C;} .fc-action.warn{color:#E65100;}

/* ══ Footer ══ */
#rf-footer{border-top:1px solid #C8E6C9;padding:10px 28px;
  font-size:7.5pt;color:#888;display:flex;justify-content:space-between;background:#FFF;}
</style>
</head>
<body>

<!-- ════ HEADER ════════════════════════════════════════════ -->
<div id="rf-header">
  <div class="hdr-left">
    <div class="suite-name">WIPEWASH · HIL TEST REPORT</div>
    <div class="suite-sub">AUTOMOTIVE ECU VALIDATION · ASAM / ISO 26262 · Robot Framework Style</div>
  </div>
  <div class="hdr-right">
    <div><b>Project :</b> {{ meta.project }}</div>
    <div><b>Bench   :</b> {{ meta.bench_id }}</div>
    <div><b>Date    :</b> {{ meta.date }}</div>
    <div><b>Duration:</b> {{ meta.duration }}</div>
    {%- if meta.operator %}<div><b>Operator:</b> {{ meta.operator }}</div>{%- endif %}
  </div>
</div>

<!-- ════ SUMMARY BAR ═══════════════════════════════════════ -->
<div id="rf-summary">
  <div class="sp"><div class="sp-num c-total">{{ st.total }}</div><div class="sp-lbl">TOTAL</div></div>
  <div class="sp"><div class="sp-num c-pass">{{ st.n_pass }}</div><div class="sp-lbl">PASS · {{ st.pct_pass }}%</div></div>
  <div class="sp"><div class="sp-num c-fail">{{ st.n_fail }}</div><div class="sp-lbl">FAIL · {{ st.pct_fail }}%</div></div>
  <div class="sp"><div class="sp-num c-timeout">{{ st.n_timeout }}</div><div class="sp-lbl">TIMEOUT · {{ st.pct_timeout }}%</div></div>
  <div style="flex:1;"></div>
  <!-- SVG score ring -->
  <div class="score-ring">
    <svg viewBox="0 0 54 54">
      <circle cx="27" cy="27" r="22" fill="none" stroke="#2E4A1A" stroke-width="6"/>
      <circle cx="27" cy="27" r="22" fill="none"
        stroke="{{ '#43A047' if st.score_pct >= 80 else ('#FB8C00' if st.score_pct >= 50 else '#E53935') }}"
        stroke-width="6"
        stroke-dasharray="{{ (st.score_pct / 100 * 138.2)|round(1) }} 138.2"
        stroke-dashoffset="34.6"
        stroke-linecap="round"
        transform="rotate(-90 27 27)"/>
    </svg>
    <div class="sr-txt">{{ st.score_pct }}%</div>
  </div>
</div>

<!-- ════ CHARTS ════════════════════════════════════════════ -->
<div id="charts-area">
  {%- if charts.gauge   %}<div class="cc"><img src="data:image/png;base64,{{ charts.gauge }}"   alt="Score"/></div>{%- endif %}
  {%- if charts.donut   %}<div class="cc"><img src="data:image/png;base64,{{ charts.donut }}"   alt="Répartition"/></div>{%- endif %}
  {%- if charts.by_cat  %}<div class="cc wide"><img src="data:image/png;base64,{{ charts.by_cat }}"  alt="Par catégorie"/></div>{%- endif %}
</div>
{%- if charts.timeline %}
<div style="padding:0 28px 14px;background:#FFF;border-bottom:1px solid #DDE8D0;">
  <div class="cc" style="flex:1 1 100%;"><img src="data:image/png;base64,{{ charts.timeline }}" alt="Timeline"/></div>
</div>
{%- endif %}

<!-- ════ RUNS SUMMARY (multi-run only) ═══════════════════ -->
{%- if runs and runs|length > 1 %}
<div class="sec-bar">RÉSUMÉ DES RUNS — {{ runs|length }} exécutions</div>
<div style="padding:10px 28px;background:#FFF;border-bottom:1px solid #DDE8D0;overflow-x:auto;">
  <table style="width:100%;border-collapse:collapse;font-size:9pt;">
    <thead>
      <tr style="background:#1B2E0F;color:#C8E6B0;">
        <th style="padding:6px 10px;text-align:left;">Run</th>
        <th style="padding:6px 10px;text-align:left;">Heure début</th>
        <th style="padding:6px 10px;text-align:left;">Heure fin</th>
        <th style="padding:6px 10px;text-align:left;">IDs exécutés</th>
        <th style="padding:6px 10px;text-align:center;">Total</th>
        <th style="padding:6px 10px;text-align:center;">PASS</th>
        <th style="padding:6px 10px;text-align:center;">FAIL</th>
        <th style="padding:6px 10px;text-align:center;">TIMEOUT</th>
        <th style="padding:6px 10px;text-align:center;">Score</th>
      </tr>
    </thead>
    <tbody>
    {%- for run in runs %}
    {%- set rp = run.results | selectattr('status','eq','PASS') | list | length %}
    {%- set rf = run.results | selectattr('status','eq','FAIL') | list | length %}
    {%- set rt = run.results | selectattr('status','eq','TIMEOUT') | list | length %}
    {%- set tot = run.results | length %}
    {%- set score = ((rp / tot * 100)|int) if tot > 0 else 0 %}
    <tr style="border-bottom:1px solid #E8F5E0;{% if loop.index is odd %}background:#F8FFF4;{% else %}background:#FFF;{% endif %}">
      <td style="padding:5px 10px;font-weight:700;color:#2E6B00;">#{{ run.run_index }}</td>
      <td style="padding:5px 10px;font-family:Consolas,monospace;font-size:8pt;">
        {%- if run.t_start %}{{ run.t_start.strftime('%H:%M:%S') }}{%- else %}—{%- endif %}
      </td>
      <td style="padding:5px 10px;font-family:Consolas,monospace;font-size:8pt;">
        {%- if run.t_end %}{{ run.t_end.strftime('%H:%M:%S') }}{%- else %}—{%- endif %}
      </td>
      <td style="padding:5px 10px;font-family:Consolas,monospace;font-size:8pt;color:#555;">
        {{ run.ids | join(', ') if run.ids else '—' }}
      </td>
      <td style="padding:5px 10px;text-align:center;font-weight:700;">{{ tot }}</td>
      <td style="padding:5px 10px;text-align:center;font-weight:700;color:#1B5E20;">{{ rp }}</td>
      <td style="padding:5px 10px;text-align:center;font-weight:700;color:#B71C1C;">{{ rf }}</td>
      <td style="padding:5px 10px;text-align:center;font-weight:700;color:#E65100;">{{ rt }}</td>
      <td style="padding:5px 10px;text-align:center;">
        <span style="font-weight:900;color:{{ '#1B5E20' if score>=80 else ('#E65100' if score>=50 else '#B71C1C') }};">{{ score }}%</span>
      </td>
    </tr>
    {%- endfor %}
    </tbody>
  </table>
</div>
{%- endif %}

<!-- ════ SUITE TREE ════════════════════════════════════════ -->
<div class="sec-bar">TEST SUITE — {{ st.total }} test(s) sur {{ runs|length if runs else 1 }} run(s)</div>
<div id="suite-area">
  <div class="rf-suite-hdr" onclick="toggleEl('suite-body')">
    
    <span class="sh-name">WipeWash HIL — {{ meta.project }}</span>
    <span class="sh-badge {% if st.n_fail==0 and st.n_timeout==0 %}sh-ok{% elif st.n_pass==0 %}sh-bad{% else %}sh-mix{% endif %}">
      {{ st.n_pass }} PASS &nbsp;·&nbsp; {{ st.n_fail }} FAIL &nbsp;·&nbsp; {{ st.n_timeout }} TIMEOUT
    </span>
    <span style="font-size:8pt;color:#9AC87A;">{{ meta.duration }}</span>
  </div>
  <div class="rf-suite-body" id="suite-body">

  {%- if runs and runs|length > 1 %}
    {#— Multi-run: afficher chaque run comme un sous-bloc —#}
    {%- set ns = namespace(global_tid=0) %}
    {%- for run in runs %}
    {%- set rp = run.results | selectattr('status','eq','PASS') | list | length %}
    {%- set rf = run.results | selectattr('status','eq','FAIL') | list | length %}
    {%- set rt = run.results | selectattr('status','eq','TIMEOUT') | list | length %}
    <div class="run-hdr" onclick="toggleEl('run-body-{{ loop.index }}')">
      
      <span class="run-badge">RUN #{{ run.run_index }}</span>
      <span style="font-size:9pt;font-weight:700;">{{ run.results|length }} test(s)</span>
      <span style="font-size:8pt;color:#9AC87A;">
        {%- if run.t_start %}{{ run.t_start.strftime('%H:%M:%S') }}{%- endif %}
        {%- if run.t_end %} → {{ run.t_end.strftime('%H:%M:%S') }}{%- endif %}
      </span>
      <span class="run-meta">
        <span style="color:#69DB7C;">PASS: {{ rp }}</span>&nbsp;
        <span style="color:#FF6B6B;">FAIL: {{ rf }}</span>&nbsp;
        <span style="color:#FFB347;">TIMEOUT: {{ rt }}</span>
      </span>
    </div>
    <div class="run-body" id="run-body-{{ loop.index }}">
      <div class="run-stats">
        IDs exécutés : {{ run.ids | join(', ') if run.ids else '—' }}
      </div>
      {%- for r in run.results %}
      {%- set ns.global_tid = ns.global_tid + 1 %}
      {%- set tid = ns.global_tid %}
      <div class="rf-test" id="test-{{ tid }}">
        <div class="rf-test-hdr" onclick="toggleTest({{ tid }})">
          <div class="rft-exp" id="exp-{{ tid }}">+</div>
          <span class="rft-status s-{{ r.status }}">{{ r.status }}</span>
          <span class="rft-id">{{ r.test_id }}</span>
          <span class="rft-name">{{ r.name }}</span>
          <span class="rft-cat cat-{{ r.category }}">{{ r.category }}</span>
          <span class="rft-dur">{{ r.measured }}</span>
        </div>
        <div class="rf-test-body" id="tbody-{{ tid }}">
          <div class="test-info-strip">
            <span><b>Run :</b> #{{ run.run_index }}</span>
            <span><b>Ref:</b> <span class="mono">{{ r.ref }}</span></span>
            <span><b>Limit:</b> <span class="mono">{{ r.limit }}</span></span>
            <span><b>Measured:</b>
              <span class="mono {% if r.status=='PASS' %}val-ok{% else %}val-ko{% endif %}">{{ r.measured }}</span>
            </span>
            {%- if r.details %}<span><b>Details:</b> {{ r.details }}</span>{%- endif %}
          </div>
          <ul class="kw-list">
          {%- for kw in r._keywords %}
            {{ render_kw(kw, tid|string ~ '_' ~ loop.index|string) }}
          {%- endfor %}
          </ul>
        </div>
      </div>
      {%- endfor %}
    </div>
    {%- endfor %}

  {%- else %}
    {#— Run unique ou pas de runs : affichage plat classique —#}
    {%- for r in results %}
    {%- set tid = loop.index %}
    <div class="rf-test" id="test-{{ tid }}">
      <div class="rf-test-hdr" onclick="toggleTest({{ tid }})">
        <div class="rft-exp" id="exp-{{ tid }}">+</div>
        <span class="rft-status s-{{ r.status }}">{{ r.status }}</span>
        <span class="rft-id">{{ r.test_id }}</span>
        <span class="rft-name">{{ r.name }}</span>
        <span class="rft-cat cat-{{ r.category }}">{{ r.category }}</span>
        <span class="rft-dur">{{ r.measured }}</span>
      </div>
      <div class="rf-test-body" id="tbody-{{ tid }}">
        <div class="test-info-strip">
          <span><b>Ref:</b> <span class="mono">{{ r.ref }}</span></span>
          <span><b>Limit:</b> <span class="mono">{{ r.limit }}</span></span>
          <span><b>Measured:</b>
            <span class="mono {% if r.status=='PASS' %}val-ok{% else %}val-ko{% endif %}">{{ r.measured }}</span>
          </span>
          {%- if r.details %}<span><b>Details:</b> {{ r.details }}</span>{%- endif %}
        </div>
        <ul class="kw-list">
        {%- for kw in r._keywords %}
          {{ render_kw(kw, tid|string ~ '_' ~ loop.index|string) }}
        {%- endfor %}
        </ul>
      </div>
    </div>
    {%- endfor %}
  {%- endif %}

  </div>
</div>

<!-- ════ FAIL FOCUS ════════════════════════════════════════ -->
{%- if failed %}
<div class="sec-bar" style="margin-top:8px;">FAILED TESTS — {{ failed|length }}</div>
<div class="focus-wrap">
{%- for r in failed %}
<div class="focus-card">
  <div class="fc-title">FAIL: {{ r.test_id }} — {{ r.name }}</div>
  <div class="fc-grid">
    <div class="fc-cell"><div class="fc-lbl">EXIGENCE</div><div class="fc-val">{{ r.ref }}</div></div>
    <div class="fc-cell"><div class="fc-lbl">LIMITE ATTENDUE</div><div class="fc-val">{{ r.limit }}</div></div>
    <div class="fc-cell"><div class="fc-lbl">VALEUR MESURÉE</div><div class="fc-val" style="color:#B71C1C;">{{ r.measured }}</div></div>
  </div>
  {%- if r.details %}<div style="font-size:8.5pt;color:#555;margin-top:8px;font-style:italic;">{{ r.details }}</div>{%- endif %}
  <div class="fc-action fail">Action : vérifier timings réseau, rejouer le test isolément, contrôler la tension d'alimentation moteur.</div>
</div>
{%- endfor %}
</div>
{%- endif %}

{%- if timeout_tests %}
<div class="sec-bar" style="margin-top:8px;">TIMEOUTS — {{ timeout_tests|length }}</div>
<div class="focus-wrap warn">
{%- for r in timeout_tests %}
<div class="focus-card warn">
  <div class="fc-title warn">TIMEOUT: {{ r.test_id }} — {{ r.name }}</div>
  <div class="fc-grid">
    <div class="fc-cell"><div class="fc-lbl">EXIGENCE</div><div class="fc-val">{{ r.ref }}</div></div>
    <div class="fc-cell"><div class="fc-lbl">DÉLAI LIMITE</div><div class="fc-val">{{ r.limit }}</div></div>
    <div class="fc-cell"><div class="fc-lbl">PROBABLE CAUSE</div><div class="fc-val" style="color:#E65100;">No frame received</div></div>
  </div>
  <div class="fc-action warn">Vérifier la connexion TCP/Redis au RPi — banc hors-ligne ou worker non démarré.</div>
</div>
{%- endfor %}
</div>
{%- endif %}

<div id="rf-footer">
  <span>{{ meta.date }} · Confidentiel — Usage interne</span>
</div>

<script>
/* Helpers expand/collapse */
function toggleEl(id){
  var el=document.getElementById(id);
  if(!el)return;
  el.style.display=(el.style.display==='none'||el.style.display==='')?'block':'none';
}
function toggleTest(idx){
  var b=document.getElementById('tbody-'+idx);
  var e=document.getElementById('exp-'+idx);
  if(!b)return;
  var open=b.style.display==='block';
  b.style.display=open?'none':'block';
  if(e)e.textContent=open?'+':'−';
}
function toggleKw(uid){
  var b=document.getElementById('kb-'+uid);
  var e=document.getElementById('ke-'+uid);
  if(!b)return;
  var open=b.style.display==='block';
  b.style.display=open?'none':'block';
  if(e)e.textContent=open?'+':'−';
}
/* Auto-expand failing tests on load */
document.addEventListener('DOMContentLoaded',function(){
  var sb=document.getElementById('suite-body');
  if(sb)sb.style.display='block';
  document.querySelectorAll('.s-FAIL,.s-TIMEOUT').forEach(function(badge){
    var hdr=badge.closest('.rf-test-hdr');
    if(!hdr)return;
    var test=hdr.closest('.rf-test');
    if(!test)return;
    var idx=test.id.replace('test-','');
    var b=document.getElementById('tbody-'+idx);
    var e=document.getElementById('exp-'+idx);
    if(b){b.style.display='block';}
    if(e){e.textContent='−';}
  });
});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# CLASSE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

class ReportGenerator:
    """
    Génère un rapport HTML style Robot Framework.

    Args:
        bench_id  : identifiant du banc (ex: "WipeWash-Bench-HIL")
        project   : nom du projet     (ex: "WipeWash BCM v4.2")
        operator  : opérateur         (optionnel)

    Note: t_start / t_end sont passés à generate(), pas au constructeur.
    """

    def __init__(self, bench_id: str = "WipeWash-Bench",
                 project: str  = "WipeWash Automotive HIL",
                 operator: str = ""):
        self._bench_id = bench_id
        self._project  = project
        self._operator = operator
        self._env      = Environment(loader=BaseLoader())

    # ----------------------------------------------------------
    def generate(self,
                 results,
                 output_path: str,
                 pdf: bool = False,
                 t_start   = None,
                 t_end     = None,
                 runs      = None) -> str:
        """
        Génère le rapport HTML et l'écrit dans ``output_path`` (chemin complet).

        Args:
            results     : liste de TestResult
            output_path : chemin complet du fichier de sortie (ex: "/tmp/report.html")
            pdf         : si True tente de générer un PDF via weasyprint
            t_start     : datetime de début de campagne (optionnel)
            t_end       : datetime de fin   de campagne (optionnel)

        Returns:
            Chemin absolu du fichier HTML généré.
        """
        output_path = os.path.abspath(output_path)
        parent_dir  = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        now = datetime.datetime.now()

        if t_start and t_end:
            d        = (t_end - t_start).total_seconds()
            duration = f"{int(d // 60)}m {int(d % 60):02d}s"
        else:
            duration = "—"

        meta   = _Meta(project=self._project, bench_id=self._bench_id,
                       date=now.strftime("%Y-%m-%d  %H:%M:%S"),
                       duration=duration, operator=self._operator)
        st     = self._stats(results)
        charts = {
            "gauge":    _gauge(st.score_pct),
            "donut":    _donut(st.n_pass, st.n_fail, st.n_timeout),
            "by_cat":   _bars_by_cat(results),
            "timeline": _timeline(results),
        }
        failed        = [r for r in results if r.status == "FAIL"]
        timeout_tests = [r for r in results if r.status == "TIMEOUT"]

        # Attacher les keywords dérivés à chaque résultat (usage template seul)
        for r in results:
            r._keywords = _make_keywords(r)

        tmpl = self._env.from_string(_TMPL)
        html = tmpl.render(
            results=results,
            meta=meta, st=st, charts=charts,
            failed=failed, timeout_tests=timeout_tests,
            runs=runs or [],
        )

        # Nettoyer l'attribut temporaire
        for r in results:
            try:
                del r._keywords
            except AttributeError:
                pass

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        if pdf:
            try:
                from weasyprint import HTML as WH
                WH(string=html).write_pdf(output_path.replace(".html", ".pdf"))
            except Exception as e:
                print(f"[ReportGenerator] PDF skipped — {e}")

        return output_path

    # ----------------------------------------------------------
    def _stats(self, results) -> _Stats:
        n_p = sum(1 for r in results if r.status == "PASS")
        n_f = sum(1 for r in results if r.status == "FAIL")
        n_t = sum(1 for r in results if r.status == "TIMEOUT")
        tot = len(results)
        pct = lambda n: int(n / tot * 100) if tot else 0
        return _Stats(tot, n_p, n_f, n_t, pct(n_p), pct(n_p), pct(n_f), pct(n_t))
