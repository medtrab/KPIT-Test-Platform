"""
car_html_widget.py — Deux widgets voiture pour la plateforme HIL.

CarHTMLWidget   → car_controldesk_v5_modified.html  (onglet LIN / CRS / Vehicle)
CarXRayWidget   → controldesk-xray.html             (onglet Motor / Pump)

Les deux héritent de _CarBaseWidget qui gère le chargement HTML,
la file JS et l'API Python commune.
"""

from pathlib import Path

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore    import QWebEngineSettings, QWebEnginePage
from PySide6.QtCore             import QUrl, QTimer, Qt
from PySide6.QtGui              import QColor


# ════════════════════════════════════════════════════════════════════════════
#  JS injecté pour car_controldesk_v5_modified.html  (onglet LIN / CRS / Vehicle)
#  Masque TOUS les panneaux de contrôle HTML — seule la vue 3D reste visible.
#  Le comportement est identique à l'ancien car_simulator.html :
#  Python pilote tout via window.controlAPI (setWiperOp, setRain, etc.)
# ════════════════════════════════════════════════════════════════════════════
_SIMULATOR_API_JS = """
<style>
  /* ── Masquer TOUS les panneaux de contrôle HTML ── */
  #ctrl-panel,
  #avatar-switcher,
  #selector-toggle,
  #sel-breadcrumb,
  #sel-clear,
  #comp-selector-panel,
  #comp-inspector,
  #hint,
  #mesh-debug-panel,
  #mesh-toggle-btn,
  #mesh-sel-label
  { display: none !important; }

  /* ── Viewer plein écran, pas de marges ── */
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    overflow: hidden !important;
    background: #0a0f0a !important;
  }
  #viewer-wrap {
    width: 100vw !important;
    height: 100vh !important;
  }
  #three-container {
    width: 100vw !important;
    height: 100vh !important;
  }
  #viewer-header {
    position: absolute !important;
    top: 4px !important;
    left: 8px !important;
    z-index: 10 !important;
    pointer-events: none !important;
  }
  #loading-screen { background: #0a0f0a !important; }
  #wave-bg { display: none !important; }
</style>
<script>
window.addEventListener('load', function () {

  window.controlAPI = {

    setSpeed: function (v) {
      v = parseFloat(v) || 0;
      speed = Math.max(0, Math.min(200, v));
      if (typeof updateGauges    === 'function') updateGauges();
      if (typeof drawSpeedometer === 'function') drawSpeedometer();
    },

    /* Mémorise le % pluie courant pour adapter la vitesse AUTO en temps réel */
    _rainPct: 0,

    setRain: function (pct) {
      pct = parseFloat(pct) || 0;
      window.controlAPI._rainPct = pct;
      var mode = pct <= 0 ? 'off' : pct <= 30 ? 'light' : pct <= 65 ? 'medium' : 'heavy';
      if (typeof setRain === 'function') setRain(mode);
      /* Si on est déjà en mode AUTO, re-appliquer la vitesse correcte */
      if (window.controlAPI._currentOp === 4) {
        window.controlAPI._applyAutoSpeed();
      }
    },

    /* Calcule et applique la vitesse correcte pour le mode AUTO :
       rain < 20 → SPEED1 (slow) | rain >= 20 → SPEED2 (fast)
       Le rear wiper est toujours OFF en AUTO. */
    _applyAutoSpeed: function () {
      var speed = window.controlAPI._rainPct < 20 ? 'slow' : 'fast';
      if(typeof setFrontWiper==='function') setFrontWiper(speed);
      if(typeof setRearWiper ==='function') setRearWiper ('off');
    },

    /* Opération wiper courante (0-7) — utilisée pour re-sync après setRain */
    _currentOp: 0,

    setGear: function (g) {
      if (typeof setGear === 'function') setGear(g);
    },

    setIgnition: function (state) {
      var s = (state || '').toLowerCase();
      if (s === 'on' || s === 'acc' || s === 'off') {
        if (typeof setIgnition === 'function') setIgnition(s);
      }
    },

    setWiperOp: function (op) {
      op = parseInt(op);
      window.controlAPI._currentOp = op;
      switch (op) {
        case 0:
          if(typeof setFrontWiper==='function') setFrontWiper('off');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
          break;
        case 1:
          if(typeof setFrontWiper==='function') setFrontWiper('slow');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
          break;
        case 2:
          if(typeof setFrontWiper==='function') setFrontWiper('slow');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
          break;
        case 3:
          if(typeof setFrontWiper==='function') setFrontWiper('fast');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
          break;
        case 4:
          /* AUTO : rain < 20 → SPEED1 (slow) | rain >= 20 → SPEED2 (fast)
             Le rear wiper est toujours OFF en mode AUTO. */
          window.controlAPI._applyAutoSpeed();
          break;
        case 5:
          if(typeof setFrontWiper==='function') setFrontWiper('slow');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
          if(typeof startWash    ==='function') startWash('front');
          break;
        case 6:
          if(typeof setFrontWiper==='function') setFrontWiper('off');
          if(typeof setRearWiper ==='function') setRearWiper ('on');
          if(typeof startWash    ==='function') startWash('rear');
          break;
        case 7:
          if(typeof setFrontWiper==='function') setFrontWiper('off');
          if(typeof setRearWiper ==='function') setRearWiper ('on');
          break;
        default:
          if(typeof setFrontWiper==='function') setFrontWiper('off');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
      }
    },

    setWiperFromBCM: function (motor_on, op, state) {
      var isRearOp = (op === 6 || op === 7);
      if (motor_on || isRearOp) {
        window.controlAPI.setWiperOp(op);
      } else {
        if (op === 0) {
          if(typeof setFrontWiper==='function') setFrontWiper('off');
          if(typeof setRearWiper ==='function') setRearWiper ('off');
        }
      }
    }
  };

  console.log('[CarHTMLWidget] controlAPI ready — car_simulator');
});
</script>
"""


# ════════════════════════════════════════════════════════════════════════════
#  JS injecté pour controldesk-xray.html  (onglet Motor / Pump)
# ════════════════════════════════════════════════════════════════════════════
_XRAY_API_JS = """
<style>
  /* Masquer panneaux manuels — l'UI Python les remplace */
  .ww-panel, #bottom-controls,
  .lock-btn, #lock-btn, #lock-status, .lock-bar,
  .wmode-row, .wmode-panel,
  .accel-bar, .accel-btn,
  .door-panel
  { display: none !important; }

  /* ── Masquer trunk et logo BMW ── */
  #path32798, #path32797,
  .xray-trunk,
  .path32797-toggle {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
  }

  /* ── Masquer le logo BMW (kidney grille + cercle badge) ── */
  /* Les rects du grille réniforme + cercles + paths bleus/blancs du badge */
  rect[x="89"], rect[x="96.4"],
  circle[cx="96.3"],
  path[d^="m 96.3,78"],
  path[d^="m 96.3,83"],
  path[d^="m 93.8,80.5"],
  path[d^="m 98.8,80.5"] {
    display: none !important;
  }

  /* ── Portes colorées en noir ── */
  .xray-door path, .xray-door rect {
    fill: rgba(10, 10, 10, 0.55) !important;
    stroke: #1a1a1a !important;
  }
  .xray-door > path:first-child {
    fill: rgba(15, 15, 15, 0.70) !important;
    stroke: #111111 !important;
    stroke-width: 2.0 !important;
    filter: drop-shadow(0 0 2px rgba(0,0,0,0.8));
  }
  .xray-door > path:nth-child(2) {
    fill: rgba(5, 5, 5, 0.40) !important;
    stroke: rgba(40, 40, 40, 0.80) !important;
  }

  /* ── Animation d'entrée : slide du haut vers le bas avec cercle ── */
  @keyframes carSlideDown {
    0%   { opacity: 0;   transform: translateY(-120%) scale(0.90); }
    55%  { opacity: 1;   transform: translateY(6%)    scale(1.01); }
    75%  { transform: translateY(-2%) scale(0.995); }
    100% { opacity: 1;   transform: translateY(0%)    scale(1.0);  }
  }
  .car-stage {
    animation: carSlideDown 0.90s cubic-bezier(0.22, 1, 0.36, 1) forwards !important;
  }

  body {
    padding-top: 4px !important;
    background: #FFFFFF !important;
    align-items: center !important;
    justify-content: flex-start !important;
  }
  .bg-grid, .bg-glow { display: block !important; }
  .status-bar { margin-bottom: 4px !important; font-size: 8.5px !important; }
  .hud-title  { margin-top: 4px !important; margin-bottom: 4px !important; }
  .main-layout {
    justify-content: center !important;
    width: 100% !important;
    flex-wrap: wrap !important;
    gap: 10px !important;
  }
  /* ── Remplissage viewport complet ── */
  html, body {
    height: 100vh !important;
    overflow: hidden !important;
    padding: 0 !important;
    margin: 0 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
  }
  .main-layout {
    height: 100vh !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0 !important;
  }
  .bg-grid, .bg-glow { display: none !important; }
  .car-stage {
    transform-origin: center center !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
  }
  /* SUPPRESSION DU FOND CIRCULAIRE */
  .showroom-bg {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    width: auto !important;
    height: auto !important;
    border-radius: 0 !important;
    overflow: visible !important;
  }
  .status-bar, .hud-title { display: none !important; }
  body { background: #FFFFFF !important; }
</style>

<script>
/* ── Auto-scale : voiture centrée qui remplit toute la hauteur ── */
(function () {
  var CAR_H = 400;
  var CAR_W = 400;
  function scaleCarToFit () {
    var stage = document.querySelector('.car-stage');
    if (!stage) return;
    var vh = window.innerHeight;
    var vw = window.innerWidth;
    var scale = Math.min(vh / CAR_H, vw / CAR_W) * 0.98;
    stage.style.transform = 'scale(' + scale + ')';
    stage.style.width  = CAR_W + 'px';
    stage.style.height = CAR_H + 'px';
  }
  window.scaleCarToFit = scaleCarToFit;
  window.addEventListener('load',   scaleCarToFit);
  window.addEventListener('resize', scaleCarToFit);
  if (document.readyState === 'complete') scaleCarToFit();
})();

window.addEventListener('load', function () {

  window.controlAPI = {

    setSpeed: function (v) {
      v = parseFloat(v) || 0;
      speed = Math.max(0, Math.min(200, v));
      if (typeof updateGauges    === 'function') updateGauges();
      if (typeof drawSpeedometer === 'function') drawSpeedometer();
    },

    /* Mémorise le % pluie courant pour adapter la vitesse AUTO en temps réel */
    _rainPct: 0,

    setRain: function (pct) {
      pct = parseFloat(pct) || 0;
      window.controlAPI._rainPct = pct;
      var mode = pct <= 0 ? 'off' : pct <= 30 ? 'light' : pct <= 65 ? 'medium' : 'heavy';
      if (typeof setRain === 'function') setRain(mode);
      /* Si on est déjà en mode AUTO, re-appliquer la vitesse correcte */
      if (window.controlAPI._currentOp === 4) {
        window.controlAPI._applyAutoSpeed();
      }
    },

    /* Calcule et applique la vitesse correcte pour le mode AUTO :
       rain < 20 → SPEED1 (slow) | rain >= 20 → SPEED2 (fast)
       Le rear wiper est toujours OFF en AUTO. */
    _applyAutoSpeed: function () {
      var speed = window.controlAPI._rainPct < 20 ? 'slow' : 'fast';
      if (typeof setFrontWiper === 'function') setFrontWiper(speed);
      if (typeof setRearWiper  === 'function') setRearWiper('off');
    },

    /* Opération wiper courante (0-7) — utilisée pour re-sync après setRain */
    _currentOp: 0,

    setGear: function (g) {
      if (typeof g === 'boolean') g = g ? 'R' : 'D';
      if (typeof setGear === 'function') setGear(g);
    },

    setIgnition: function (state) {
      var s = (state || 'off').toLowerCase();
      if (s !== 'on' && s !== 'acc') s = 'off';
      if (typeof setIgnition === 'function') setIgnition(s);
    },

    setPump: function (active) {
      if (active) { if (typeof startPump === 'function') startPump(); }
      else         { if (typeof stopPump  === 'function') stopPump();  }
    },

    /* WOP 0-7 */
    setWiperOp: function (op) {
      op = parseInt(op);
      window.controlAPI._currentOp = op;
      var isWash = (op === 5 || op === 6);
      if (!isWash) {
        if (typeof stopWash === 'function') { stopWash('front'); stopWash('rear'); }
        if (typeof stopPump === 'function') stopPump();
      }
      switch (op) {
        case 0:
          if (typeof setFrontWiper === 'function') setFrontWiper('off');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
          break;
        case 1:
          if (typeof setFrontWiper === 'function') setFrontWiper('slow');
          setTimeout(function(){
            if (typeof setFrontWiper === 'function') setFrontWiper('off');
          }, 2800);
          break;
        case 2:
          if (typeof setFrontWiper === 'function') setFrontWiper('slow');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
          break;
        case 3:
          if (typeof setFrontWiper === 'function') setFrontWiper('fast');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
          break;
        case 4:
          /* AUTO : rain < 20 → SPEED1 (slow) | rain >= 20 → SPEED2 (fast)
             Le rear wiper est toujours OFF en mode AUTO. */
          window.controlAPI._applyAutoSpeed();
          break;
        case 5:
          if (typeof setFrontWiper === 'function') setFrontWiper('slow');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
          if (typeof startPump     === 'function') startPump();
          if (typeof startWash     === 'function') startWash('front');
          break;
        case 6:
          if (typeof setFrontWiper === 'function') setFrontWiper('off');
          if (typeof setRearWiper  === 'function') setRearWiper('on');
          if (typeof startPump     === 'function') startPump();
          if (typeof startWash     === 'function') startWash('rear');
          break;
        case 7:
          if (typeof setFrontWiper === 'function') setFrontWiper('off');
          if (typeof setRearWiper  === 'function') setRearWiper('on');
          break;
        default:
          if (typeof setFrontWiper === 'function') setFrontWiper('off');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
      }
    },

    setWiperFromBCM: function (motor_on, op, state) {
      var isRearOp = (op === 6 || op === 7);
      if (motor_on || isRearOp) {
        window.controlAPI.setWiperOp(op);
      } else {
        if (op === 0) {
          if (typeof setFrontWiper === 'function') setFrontWiper('off');
          if (typeof setRearWiper  === 'function') setRearWiper('off');
          if (typeof stopPump      === 'function') stopPump();
        }
      }
    },

    /* Pompe hydraulique : impeller + fluid lines */
    setPumpState: function (state, fault) {
      var active = !fault && (state === 'FORWARD' || state === 'BACKWARD');
      window.controlAPI.setPump(active);
      var carEl = document.getElementById('car');
      if (carEl) {
        carEl.classList.toggle('pump-active',       active);
        carEl.classList.toggle('pump-front-active', active && state === 'FORWARD');
        carEl.classList.toggle('pump-rear-active',  active && state === 'BACKWARD');
      }
    }
  };

  console.log('[CarXRayWidget] controlAPI ready — controldesk-xray');

  /* ── Suppression trunk + logo BMW au chargement ── */
  (function hideTrunkAndBadge() {
    // Trunk paths
    ['path32798', 'path32797'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    // BMW kidney grille + badge : on cible les éléments SVG par parcours DOM
    // Le bloc badge est juste après le commentaire "BMW KIDNEY GRILLE"
    // On parcourt tous les rect/circle/path du SVG et on cache ceux qui forment le logo
    var svg = document.querySelector('svg');
    if (!svg) return;
    // kidney rects (x≈89 et x≈96.4), badge circles (cx≈96.3), badge paths
    var allEls = svg.querySelectorAll('rect, circle, path');
    allEls.forEach(function(el) {
      var x   = parseFloat(el.getAttribute('x')   || el.getAttribute('cx') || 0);
      var y   = parseFloat(el.getAttribute('y')   || el.getAttribute('cy') || 0);
      var d   = el.getAttribute('d') || '';
      // Kidney grille rects
      if (el.tagName === 'rect' && ((x >= 88 && x <= 90) || (x >= 95 && x <= 98)) && y >= 74 && y <= 78)
        el.style.display = 'none';
      // Badge circles cx≈96.3 cy≈80.5
      if (el.tagName === 'circle' && x >= 95 && x <= 98 && y >= 79 && y <= 82)
        el.style.display = 'none';
      // Badge coloured quadrant paths (start with "m 96.3" or "m 93.8" or "m 98.8")
      if (el.tagName === 'path' && (d.startsWith('m 96.3,7') || d.startsWith('m 96.3,8') ||
          d.startsWith('m 93.8') || d.startsWith('m 98.8')))
        el.style.display = 'none';
    });
  })();
});
</script>
"""


# ════════════════════════════════════════════════════════════════════════════
#  Injection CSS/JS pour car_controldesk_v11.html (onglet Motor/Pump)
#  — masque le ctrl-panel HTML et les panneaux debug/sélecteur
#  — adapte le layout pour remplir le QWebEngineView
# ════════════════════════════════════════════════════════════════════════════
_CONTROLDESK_V11_JS = """
<style>
  /* ── Masquer les panneaux de contrôle HTML (remplacés par PyQt) ── */
  #ctrl-panel,
  #avatar-switcher,
  #mesh-debug-panel,
  #mesh-toggle-btn,
  #mesh-sel-label,
  #selector-toggle,
  #sel-clear,
  #sel-breadcrumb,
  #comp-selector-panel,
  #comp-inspector,
  #hint
  { display: none !important; }

  /* ── Viewer plein écran ── */
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    overflow: hidden !important;
    background: #0a0f0a !important;
  }
  #viewer-wrap {
    width: 100vw !important;
    height: 100vh !important;
  }
  #three-container {
    width: 100vw !important;
    height: 100vh !important;
  }
  #viewer-header {
    position: absolute !important;
    top: 4px !important;
    left: 8px !important;
    z-index: 10 !important;
    pointer-events: none !important;
  }
  #loading-screen { background: #0a0f0a !important; }

  /* ── Supprimer l'animation de fond (points verts en vagues) ── */
  #wave-bg { display: none !important; }
</style>

<script>
/* ═══════════════════════════════════════════════════════
   controlAPI — Pont Python ↔ car_controldesk_v11.html
   Appelle les fonctions natives du HTML (setFrontWiper,
   setRearWiper, startPump, stopPump, toggleFrontMotorAnimation,
   toggleRearMotorAnimation, togglePumpAnimation, setLightsMode…)
   exactement comme le faisait controldesk-xray.html.
   ═══════════════════════════════════════════════════════ */
window.addEventListener('load', function () {

  /* ── Helpers pour activer/désactiver les animations moteur 3D ── */
  function _setFMotor(on) {
    if (typeof frontMotorAnimating === 'undefined') return;
    if (on && !frontMotorAnimating) toggleFrontMotorAnimation();
    if (!on && frontMotorAnimating)  toggleFrontMotorAnimation();
  }
  function _setRMotor(on) {
    if (typeof rearMotorAnimating === 'undefined') return;
    if (on && !rearMotorAnimating) toggleRearMotorAnimation();
    if (!on && rearMotorAnimating)  toggleRearMotorAnimation();
  }
  function _setPumpMotor(on) {
    if (typeof pumpAnimationActive === 'undefined') return;
    if (on && !pumpAnimationActive) togglePumpAnimation();
    if (!on && pumpAnimationActive)  togglePumpAnimation();
  }

  window.controlAPI = {

    /* Vitesse : affichée dans le header de la voiture si disponible */
    setSpeed: function (v) {
      v = parseFloat(v) || 0;
      /* car_controldesk_v11 n'a pas de speedometer → no-op */
    },

    /* Mémorise le % pluie courant pour adapter la vitesse AUTO en temps réel */
    _rainPct: 0,

    /* Pluie : mappe pourcentage → mode HTML */
    setRain: function (pct) {
      pct = parseFloat(pct) || 0;
      window.controlAPI._rainPct = pct;
      var mode = pct <= 0 ? 'off' : pct <= 30 ? 'light' : pct <= 65 ? 'medium' : 'heavy';
      if (typeof setRain === 'function') setRain(mode);
      /* Si on est déjà en mode AUTO, re-appliquer la vitesse correcte */
      if (window.controlAPI._currentOp === 4) {
        window.controlAPI._applyAutoSpeed();
      }
    },

    /* Calcule et applique la vitesse correcte pour le mode AUTO :
       rain < 20 → SPEED1 (slow) | rain >= 20 → SPEED2 (fast)
       Le rear wiper est toujours OFF en mode AUTO. */
    _applyAutoSpeed: function () {
      var speed = window.controlAPI._rainPct < 20 ? 'slow' : 'fast';
      setFrontWiper(speed);
      setRearWiper('off');
      _setFMotor(true);
      _setRMotor(false);
    },

    /* Opération wiper courante (0-7) — utilisée pour re-sync après setRain */
    _currentOp: 0,

    /* Gear / Reverse */
    setGear: function (g) {
      if (typeof g === 'boolean') g = g ? 'R' : 'D';
      var rev = (typeof g === 'string' && g.toUpperCase() === 'R');
      if (typeof toggleReverse === 'function') {
        var btn = document.getElementById('reverse-btn');
        var isRev = btn && btn.classList.contains('active-rev');
        if (rev && !isRev) toggleReverse();
        if (!rev && isRev)  toggleReverse();
      }
    },

    /* Reverse direct (appelé depuis _toggle_rev) */
    setReverse: function (rev) {
      this.setGear(rev ? 'R' : 'D');
    },

    /* Ignition → setLightsMode du HTML */
    setIgnition: function (state) {
      var s = (state || 'off').toLowerCase();
      if (typeof setLightsMode === 'function') {
        setLightsMode(s === 'on' ? 'on' : s === 'acc' ? 'acc' : 'off');
      }
    },

    /* ── Wiper Operation — encodage RTE BCM (identique à _XRAY_API_JS) ──────
       0 = WOP_OFF        → tout OFF
       1 = WOP_TOUCH      → front SLOW  (single wipe)  + moteur front
       2 = WOP_SPEED1     → front SLOW  (continu)      + moteur front
       3 = WOP_SPEED2     → front FAST  (continu)      + moteur front
       4 = WOP_AUTO       → front SLOW si rain<20, FAST si rain>=20
                            rear TOUJOURS OFF en AUTO
       5 = WOP_FRONT_WASH → pump forward  + moteur front + moteur pump
       6 = WOP_REAR_WASH  → pump backward + moteur rear  + moteur pump
       7 = WOP_REAR_WIPE  → rear ON       + moteur rear
    ─────────────────────────────────────────────────────────────────────── */
    setWiperOp: function (op) {
      op = parseInt(op);
      window.controlAPI._currentOp = op;
      switch (op) {
        case 0:   /* OFF */
          setFrontWiper('off');
          setRearWiper('off');
          _setFMotor(false);
          _setRMotor(false);
          break;
        case 1:   /* TOUCH — single wipe front */
          setRearWiper('off');
          _setRMotor(false);
          setFrontWiper('slow');
          _setFMotor(true);
          break;
        case 2:   /* SPEED1 — front slow continu */
          setRearWiper('off');
          _setRMotor(false);
          setFrontWiper('slow');
          _setFMotor(true);
          break;
        case 3:   /* SPEED2 — front fast continu */
          setRearWiper('off');
          _setRMotor(false);
          setFrontWiper('fast');
          _setFMotor(true);
          break;
        case 4:   /* AUTO — rain<20→slow | rain>=20→fast, rear TOUJOURS OFF */
          window.controlAPI._applyAutoSpeed();
          break;
        case 5:   /* FRONT_WASH — pump forward + front wiper */
          setRearWiper('off');
          _setRMotor(false);
          startPump('forward');
          _setFMotor(true);
          _setPumpMotor(true);
          break;
        case 6:   /* REAR_WASH — pump backward + rear wiper */
          setFrontWiper('off');
          _setFMotor(false);
          startPump('backward');
          _setRMotor(true);
          _setPumpMotor(true);
          break;
        case 7:   /* REAR_WIPE — rear wiper continu, front OFF */
          setFrontWiper('off');
          _setFMotor(false);
          _setPumpMotor(false);
          setRearWiper('on');
          _setRMotor(true);
          break;
        default:
          setFrontWiper('off');
          setRearWiper('off');
          _setFMotor(false);
          _setRMotor(false);
          break;
      }
    },

    /* ── Appel direct depuis BCM (setWiperFromBCM) ─────────────── */
    setWiperFromBCM: function (motor, op, state) {
      this.setWiperOp(op);
    },

    /* ── Pompe (depuis PumpPanel Python) ────────────────────────── */
    setPump: function (active) {
      if (active) {
        startPump('forward');
        _setPumpMotor(true);
      } else {
        stopPump('forward');
        stopPump('backward');
        _setPumpMotor(false);
      }
    },

    /* ── État pompe complet (FORWARD / BACKWARD / OFF + fault) ──── */
    setPumpState: function (state, fault) {
      if (fault) {
        stopPump('forward');
        stopPump('backward');
        _setPumpMotor(false);
        return;
      }
      if (state === 'FORWARD') {
        startPump('forward');
        _setPumpMotor(true);
      } else if (state === 'BACKWARD') {
        startPump('backward');
        _setPumpMotor(true);
      } else {
        stopPump('forward');
        stopPump('backward');
        _setPumpMotor(false);
      }
    },
  };

  console.log('[CarXRayWidget] controlAPI ready — car_controldesk_v11');
});
</script>
"""


# ════════════════════════════════════════════════════════════════════════════
#  Page silencieuse (supprime les messages JS en console)
# ════════════════════════════════════════════════════════════════════════════
class _SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        # print(f"[JS {level.name}] {source}:{line}  {message}")
        pass


# ════════════════════════════════════════════════════════════════════════════
#  Classe de base commune aux deux widgets
# ════════════════════════════════════════════════════════════════════════════
class _CarBaseWidget(QWebEngineView):
    """
    Base commune : chargement HTML, file JS, état interne, API Python.
    Sous-classes doivent définir HTML_FILE et CONTROL_API_JS.
    """

    HTML_FILE       = ""          # à surcharger
    CONTROL_API_JS  = ""          # à surcharger

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPage(_SilentPage(self))

        s = self.page().settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,   True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages,                  True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,               True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,     False)

        self._ready   = False
        self._pending: list[str] = []
        self.loadFinished.connect(self._on_loaded)

        # Fond transparent AVANT chargement — empêche le flash blanc/noir de Chromium
        self.page().setBackgroundColor(QColor(Qt.GlobalColor.transparent))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self._state = {
            "speed":      0.0,
            "rain":       0,
            "gear":       "D",
            "ign":        "off",
            "op":         0,
            "pump_state": "OFF",
            "pump_fault": False,
        }

        self._load_html()

    # ── Chargement ───────────────────────────────────────────────────────────

    def _load_html(self) -> None:
        html_path = Path(__file__).parent / self.HTML_FILE
        if not html_path.exists():
            self._show_fallback()
            return
        try:
            html = html_path.read_text(encoding="utf-8")
        except Exception as e:
            self._show_fallback(str(e))
            return
        html = html.replace("</body>", self.CONTROL_API_JS + "\n</body>")
        base_url = QUrl.fromLocalFile(str(html_path.parent) + "/")
        self.setHtml(html, base_url)

    def _show_fallback(self, err: str = "") -> None:
        self.setHtml(f"""
        <html><body style="background:#0D1117;color: #E6EDF3;font-family:monospace;
            display:flex;align-items:center;justify-content:center;height:100vh;">
        <div style="text-align:center">
            <div style="font-size:48px">&#128663;</div>
            <div style="font-size:16px;margin-top:12px">Fichier voiture introuvable</div>
            <div style="font-size:11px;color:#666;margin-top:6px">{err}</div>
            <div style="font-size:10px;color:#444;margin-top:4px">
                Placer {self.HTML_FILE} dans le même dossier que ce script</div>
        </div></body></html>
        """)

    def _on_loaded(self, ok: bool) -> None:
        self._ready = True
        # Fond transparent — supprime le fond blanc/gris du moteur Chromium
        self.page().setBackgroundColor(QColor(Qt.GlobalColor.transparent))
        self.page().runJavaScript(
            "document.documentElement.style.background='transparent';"            "document.body.style.background='transparent';"        )
        for js in self._pending:
            self.page().runJavaScript(js)
        self._pending.clear()
        QTimer.singleShot(300, self._restore_state)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Déclenche le rescale JS quand Qt redimensionne le widget
        self._js("if(typeof scaleCarToFit==='function') scaleCarToFit(); "
                 "else { var e=new Event('resize'); window.dispatchEvent(e); }")

    def _restore_state(self) -> None:
        s = self._state
        self._js(f"if(window.controlAPI) controlAPI.setIgnition('{s['ign']}');")
        self._js(f"if(window.controlAPI) controlAPI.setGear('{s['gear']}');")
        self._js(f"if(window.controlAPI) controlAPI.setSpeed({s['speed']});")
        self._js(f"if(window.controlAPI) controlAPI.setRain({s['rain']});")
        self._js(f"if(window.controlAPI) controlAPI.setWiperOp({s['op']});")

    def _js(self, code: str) -> None:
        if self._ready:
            self.page().runJavaScript(code)
        else:
            self._pending.append(code)

    # ── API Python commune ───────────────────────────────────────────────────

    def set_speed(self, speed_kmh: float) -> None:
        v = max(0.0, min(200.0, float(speed_kmh)))
        self._state["speed"] = v
        self._js(f"if(window.controlAPI) controlAPI.setSpeed({v});")

    def set_rain(self, intensity_pct: int) -> None:
        v = max(0, min(100, int(intensity_pct)))
        self._state["rain"] = v
        self._js(f"if(window.controlAPI) controlAPI.setRain({v});")

    def set_reverse(self, is_reverse: bool) -> None:
        gear = "R" if is_reverse else "D"
        self._state["gear"] = gear
        self._js(f"if(window.controlAPI) controlAPI.setGear('{gear}');")

    def set_ignition(self, state: str) -> None:
        s = (state or "off").lower()
        if s not in ("on", "off", "acc"):
            s = "off"
        self._state["ign"] = s
        self._js(f"if(window.controlAPI) controlAPI.setIgnition('{s}');")

    def set_wiper_op(self, op: int) -> None:
        op = max(0, min(7, int(op)))
        self._state["op"] = op
        self._js(f"if(window.controlAPI) controlAPI.setWiperOp({op});")

    def set_wiper_from_bcm(
        self,
        motor_on:  bool,
        rest_raw:  bool = False,
        bcm_state: str  = "OFF",
        op:        int  = 0,
    ) -> None:
        self._state["op"] = op
        js_motor = "true" if motor_on else "false"
        self._js(
            f"if(window.controlAPI) "
            f"controlAPI.setWiperFromBCM({js_motor}, {op}, '{bcm_state}');"
        )

    def set_pump_state(self, state: str, fault: bool = False) -> None:
        """Pompe hydraulique — implémentée dans CarXRayWidget, no-op ici."""
        pass


# ════════════════════════════════════════════════════════════════════════════
#  CarHTMLWidget — car_simulator.html  (onglet LIN / CRS / Vehicle)
# ════════════════════════════════════════════════════════════════════════════
class CarHTMLWidget(_CarBaseWidget):
    """
    Voiture car_controldesk_v5_modified.html pour l'onglet LIN / CRS / Vehicle.
    Utilisée pour piloter les wipers et le véhicule depuis les panneaux CRS/LIN.
    """
    HTML_FILE      = "car_controldesk_v5_modified.html"
    CONTROL_API_JS = _SIMULATOR_API_JS


# ════════════════════════════════════════════════════════════════════════════
#  CarXRayWidget — controldesk-xray.html  (onglet Motor / Pump)
# ════════════════════════════════════════════════════════════════════════════
class CarXRayWidget(_CarBaseWidget):
    """
    Voiture car_controldesk_v11.html pour l'onglet Motor / Pump.
    Affiche les animations moteur (impeller, gear rotor, fluid lines, wipers).
    """
    HTML_FILE      = "car_controldesk_v11.html"
    CONTROL_API_JS = _CONTROLDESK_V11_JS

    def _restore_state(self) -> None:
        super()._restore_state()
        s = self._state
        fault_js = str(s["pump_fault"]).lower()
        self._js(f"if(window.controlAPI) controlAPI.setPumpState('{s['pump_state']}', {fault_js});")

    def set_pump_state(self, state: str, fault: bool = False) -> None:
        """
        Anime la pompe hydraulique (impeller + fluid lines).
        state : 'FORWARD' | 'BACKWARD' | 'OFF'
        fault : True si défaut (stoppe la pompe)
        """
        self._state["pump_state"] = state
        self._state["pump_fault"] = fault
        js_fault = "true" if fault else "false"
        self._js(
            f"if(window.controlAPI) "
            f"controlAPI.setPumpState('{state}', {js_fault});"
        )