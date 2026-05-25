#!/usr/bin/env python3
"""
sim_control.py  --  RPi Simulateur : Controle manuel des modes de defaut
=========================================================================
Script autonome a lancer DIRECTEMENT sur le RPi Simulateur.
Ne necessite pas la Platform Qt.

REVISION : Cible POMPE uniquement
  [POMPE_ONLY] Suppression du choix de cible : toujours POMPE
  [POMPE_ONLY] Suppression relais SPDT (GPIO12) : inutile, plus de selection cible
  [POMPE_ONLY] Suppression relais ISO_MOT (GPIO17) : inutile, A3 lit POT moteur direct
  [POMPE_ONLY] Suppression relais ISO_DEF (GPIO27) : inutile, plus d'injection sur A3
  [POMPE_ONLY] CD4051 COM (pin 3) -> ADS1115 A0 directement (plus de detour SPDT)
  [PROTECT]    GPIO18 (ISO) TOUJOURS active EN PREMIER avant tout signal de defaut
               et desactive EN DERNIER au retour NORMAL : protection ADS1115 A0
               contre courant excessif (limite absolue 10mA, marge ~1.4mA seulement)

Corrections anterieures :
  [FIX1] Suppression de _set_cible() : causait une ouverture prematuree de ISO
         avant commutation SPDT -> noeud B flottant -> lecture basse parasite.
  [FIX2] Sequence correcte pour tous les modes :
         1. _desactiver_sources()
         2. GPIO18 HIGH EN PREMIER (isolation ADS1115 avant tout)
         3. Configurer MUX
         4. Activer source EN DERNIER
  [FIX3] _desactiver_sources() remet PWM a 0% avant de fermer ISO

  [VLOAD] Ajout mode VARIABLE LOAD :
         - IRLZ44N cable : Gate<-GPIO26(PWM 50kHz)<-[1kOhm]<-[[100nF/10kOhm vers GND]
         - Drain -> [100 Ohm 1/4W] -> Y1 CD4051 pin 14
         - Source -> GND commun
         - MUX Y1 selectionne : A=HIGH B=LOW
         - PWM 50kHz + filtre RC 100nF -> tension DC variable sur Gate
         - Tension variable transmise via MUX -> ADS1115 A0 (direct, sans SPDT)
         - ISO reste ferme : ADS1115 lit la variation normalement
         - Duty cycle reglable interactivement 0-100%

  [SHORT_TO_GND] Mode Short to GND :
         - Utilise Y0 (canal open load) avec GPIO5=HIGH
         - IRLZ44N sans resistance serie sur drain (contrairement a Y1)
         - Quand MOSFET sature : A0 tombe a ~0V direct
         - ATTENTION : partage canal Y0 avec Open Load (meme A=LOW, B=LOW)
         - Seul GPIO5 distingue les deux : LOW=open load, HIGH=short to gnd
         - [PROTECT] GPIO18 DOIT etre HIGH avant GPIO5=HIGH

Fonctions :
  - Choisir le MODE   : NORMAL | OPEN LOAD | SHORT TO VCC | VARIABLE LOAD | SHORT TO GND
  - Applique les GPIOs immediatement via RPi.GPIO
  - Cible POMPE fixe (SPDT supprime)

Usage :
  python3 sim_control.py                              # menu interactif
  python3 sim_control.py --mode "OPEN LOAD"
  python3 sim_control.py --mode "SHORT TO VCC"
  python3 sim_control.py --mode "VARIABLE LOAD" --duty 50
  python3 sim_control.py --mode "SHORT TO GND"
  python3 sim_control.py --mode NORMAL
  python3 sim_control.py --list
"""

import argparse
import sys
import time

# ------------------------------------------------------------------------------
# CONFIGURATION GPIO
# [POMPE_ONLY] GPIO12 (SPDT), GPIO17 (ISO_MOT), GPIO27 (ISO_DEF) supprimes
# ------------------------------------------------------------------------------
_FAULT_PIN_ISO       = 18   # Relais ISO     : noeud B -> A0  (LOW=connecte, actif bas)
                             # [PROTECT] TOUJOURS le premier HIGH, le dernier LOW
_FAULT_PIN_MUX_A     =  6   # CD4051 select A (LSB)
_FAULT_PIN_MUX_B     = 13   # CD4051 select B (MSB)
_FAULT_PIN_Y0_GATE   =  5   # Gate IRLZ44N   Y0 : OPEN LOAD (GPIO5 LOW) / SHORT TO GND (GPIO5 HIGH)
_FAULT_PIN_Y2_BASE   = 16   # Base 2N2222    Y2 : SHORT TO VCC

# [VLOAD] GPIO PWM pour VARIABLE LOAD
# Cablage : GPIO26 -> [1kOhm] -> Gate IRLZ44N
#                          |
#                   [100nF] -> GND  (filtre RC PWM -> DC)
#                   [10kOhm]  -> GND  (pulldown gate repos)
# Drain IRLZ44N -> [100 Ohm 1/4W] -> Y1 CD4051 pin 14
# Source IRLZ44N -> GND commun
_FAULT_PIN_VLOAD_PWM = 26   # GPIO26 rpisimulator -> filtre RC -> Gate IRLZ44N

# [VLOAD] Frequence PWM : 50kHz avec 100nF -> fc=1591Hz -> bien filtre
_VLOAD_PWM_FREQ = 50000

MODES = ["NORMAL", "OPEN LOAD", "SHORT TO VCC", "VARIABLE LOAD", "SHORT TO GND"]

# ------------------------------------------------------------------------------
# INIT GPIO
# ------------------------------------------------------------------------------
GPIO_AVAILABLE = False
GPIO = None
_pwm_vload = None   # [VLOAD] objet PWM

try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    # [PROTECT] ISO LOW au demarrage = mesure normale (relais ferme)
    GPIO.setup(_FAULT_PIN_ISO,       GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(_FAULT_PIN_MUX_A,     GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(_FAULT_PIN_MUX_B,     GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(_FAULT_PIN_Y0_GATE,   GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(_FAULT_PIN_Y2_BASE,   GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(_FAULT_PIN_VLOAD_PWM, GPIO.OUT, initial=GPIO.LOW)

    # [VLOAD] Init PWM 50kHz, duty 0% au depart
    _pwm_vload = GPIO.PWM(_FAULT_PIN_VLOAD_PWM, _VLOAD_PWM_FREQ)
    _pwm_vload.start(0)

    GPIO_AVAILABLE = True
    print("[GPIO] RPi.GPIO initialise -- cible POMPE uniquement")
    print(f"  ISO=GPIO{_FAULT_PIN_ISO}  [PROTECT: toujours premier HIGH / dernier LOW]")
    print(f"  MUX_A=GPIO{_FAULT_PIN_MUX_A}  MUX_B=GPIO{_FAULT_PIN_MUX_B}")
    print(f"  Y0=GPIO{_FAULT_PIN_Y0_GATE}  (LOW=open load | HIGH=short to gnd)")
    print(f"  Y2=GPIO{_FAULT_PIN_Y2_BASE}  VLOAD_PWM=GPIO{_FAULT_PIN_VLOAD_PWM}  freq={_VLOAD_PWM_FREQ}Hz")
    print(f"  [POMPE_ONLY] GPIO12/17/27 supprimes (SPDT/ISO_MOT/ISO_DEF retires)")

except ImportError:
    print("[GPIO] RPi.GPIO non disponible -- mode simulation (pas de GPIO reel)")
except Exception as e:
    print(f"[GPIO] Erreur init: {e} -- mode simulation")


# ------------------------------------------------------------------------------
# PRIMITIVES GPIO
# ------------------------------------------------------------------------------
def _out(pin, val):
    """Sortie GPIO si disponible, sinon log uniquement."""
    if GPIO_AVAILABLE:
        GPIO.output(pin, val)
    else:
        print(f"    [SIM] GPIO{pin} = {'HIGH' if val else 'LOW'}")


def set_variable_load(duty: float):
    """
    [VLOAD] Regle le duty cycle PWM du VARIABLE LOAD.
    duty : 0.0 a 100.0
      0%   -> IRLZ44N bloque  -> Y1 flottant -> pas de charge -> tension normale
      50%  -> IRLZ44N mi-conducteur -> charge partielle -> tension intermediaire
      100% -> IRLZ44N sature  -> Y1 tire vers GND via 100 Ohm -> tension basse (~0.198V)

    [NOTE] Resistance drain 100 Ohm protege le L298N :
           I_max = V_noeudB / 100 Ohm = 1.08V / 100 Ohm = 10.8mA -> courant tres faible
    [NOTE] Short to GND (Y0 sans resistance serie) descend a ~0V direct
    """
    duty = max(0.0, min(100.0, duty))
    if GPIO_AVAILABLE and _pwm_vload is not None:
        _pwm_vload.ChangeDutyCycle(duty)
    else:
        print(f"    [SIM] PWM GPIO{_FAULT_PIN_VLOAD_PWM} = {duty:.1f}%")
    print(f"  [VLOAD] Duty cycle : {duty:.1f}%")


def _desactiver_sources():
    """
    Eteint toutes les sources de defaut (MUX, transistors, PWM).
    [PROTECT] A appeler AVANT d'ouvrir ou de fermer ISO.
    """
    _out(_FAULT_PIN_Y0_GATE, GPIO.LOW if GPIO_AVAILABLE else 0)
    _out(_FAULT_PIN_Y2_BASE, GPIO.LOW if GPIO_AVAILABLE else 0)
    _out(_FAULT_PIN_MUX_A,   GPIO.LOW if GPIO_AVAILABLE else 0)
    _out(_FAULT_PIN_MUX_B,   GPIO.LOW if GPIO_AVAILABLE else 0)
    set_variable_load(0.0)   # [VLOAD] PWM a 0% -> IRLZ44N bloque


def _ouvrir_iso():
    """
    [PROTECT] Ouvre l'isolation : noeud B deconnecte de A0.
    DOIT etre appele APRES _desactiver_sources() et AVANT toute activation de defaut.
    GPIO18 HIGH -> relais ouvert -> A0 isole -> ADS1115 protege.
    """
    _out(_FAULT_PIN_ISO, GPIO.HIGH if GPIO_AVAILABLE else 1)


def _fermer_iso():
    """
    [PROTECT] Ferme l'isolation : noeud B connecte a A0 (mesure normale).
    DOIT etre appele APRES _desactiver_sources() et EN DERNIER au retour normal.
    GPIO18 LOW -> relais ferme -> A0 lit noeud B.
    """
    _out(_FAULT_PIN_ISO, GPIO.LOW if GPIO_AVAILABLE else 0)


# ------------------------------------------------------------------------------
# APPLICATION DES MODES
#
# [PROTECT] Sequence de securite obligatoire pour ACTIVATION defaut :
#   1. _desactiver_sources()     -- couper tout (PWM -> 0%)
#   2. _ouvrir_iso()             -- GPIO18 HIGH EN PREMIER : ADS1115 protege
#   3. Configurer MUX (A, B)
#   4. Activer source EN DERNIER (GPIO5 / GPIO16 / GPIO26)
#
# [PROTECT] Sequence de securite obligatoire pour RETOUR NORMAL :
#   1. _desactiver_sources()     -- couper tout (PWM -> 0%)
#   2. _fermer_iso()             -- GPIO18 LOW EN DERNIER
#
# [PROTECT] Exception VARIABLE LOAD : ISO reste FERME (mesure en direct)
#   -> Le risque de sequencage ne s'applique PAS : GPIO26 ne peut pas
#      injecter un courant dangereux sur A0 (100 Ohm serie sur drain)
# ------------------------------------------------------------------------------
def appliquer_mode(mode: str, duty: float = 50.0):
    """
    Applique le mode de defaut sur les GPIOs du RPi Simulateur.
    Cible : POMPE fixe (SPDT supprime, CD4051 COM -> A0 direct).

    Parametres :
        mode  : NORMAL | OPEN LOAD | SHORT TO VCC | VARIABLE LOAD | SHORT TO GND
        duty  : duty cycle PWM pour mode VARIABLE LOAD (0.0 a 100.0, defaut 50.0)

    [PROTECT] GPIO18 (ISO) est TOUJOURS le premier signal HIGH lors de l'activation
              et le DERNIER signal a revenir LOW lors du retour NORMAL.
    """
    mode = mode.upper().strip()

    if mode not in MODES:
        print(f"[ERREUR] Mode inconnu: '{mode}'. Valeurs valides: {MODES}")
        return False

    print(f"\n{'='*52}")
    print(f"  Cible : POMPE (fixe)")
    print(f"  Mode  : {mode}")
    if mode == "VARIABLE LOAD":
        print(f"  Duty  : {duty:.1f}%")
    print(f"{'='*52}")

    # -----------------------------------------------------------------------
    # ETAPE 1 : toujours couper les sources en premier (PWM -> 0%)
    # -----------------------------------------------------------------------
    _desactiver_sources()

    if mode == "NORMAL":
        # [PROTECT] Sources deja coupees -> fermer ISO EN DERNIER
        _fermer_iso()
        print(f"  GPIO  : sources OFF | PWM=0% | ISO ferme (GPIO{_FAULT_PIN_ISO}=LOW)")
        print(f"  INFO  : ADS1115 A0 lit noeud B normalement")

    elif mode == "OPEN LOAD":
        # [PROTECT] GPIO18 HIGH EN PREMIER
        _ouvrir_iso()
        # MUX Y0 : A=LOW B=LOW -> canal 0
        _out(_FAULT_PIN_MUX_A,   GPIO.LOW if GPIO_AVAILABLE else 0)
        _out(_FAULT_PIN_MUX_B,   GPIO.LOW if GPIO_AVAILABLE else 0)
        # [PROTECT] Y0 Gate reste LOW -> drain flottant = open load
        # GPIO5=LOW : pas de risque courant
        _out(_FAULT_PIN_Y0_GATE, GPIO.LOW if GPIO_AVAILABLE else 0)
        print(f"  GPIO  : ISO ouvert (GPIO{_FAULT_PIN_ISO}=HIGH) | MUX Y0 (A=LOW,B=LOW) | Y0_GATE=LOW")
        print(f"  INFO  : A0 flottant (haute impedance) -> tension proche 0V")

    elif mode == "SHORT TO GND":
        # [PROTECT] GPIO18 HIGH EN PREMIER, AVANT GPIO5=HIGH
        # Sans cet ordre : courant ~8.6mA sur A0 ADS1115 (limite absolue 10mA)
        _ouvrir_iso()
        # MUX Y0 : A=LOW B=LOW -> canal 0 (meme canal qu'open load)
        _out(_FAULT_PIN_MUX_A,   GPIO.LOW if GPIO_AVAILABLE else 0)
        _out(_FAULT_PIN_MUX_B,   GPIO.LOW if GPIO_AVAILABLE else 0)
        # [PROTECT] GPIO5=HIGH EN DERNIER (apres ISO ouvert) : IRLZ44N sature -> A0 ~0V
        # Y0 n'a PAS de resistance serie sur le drain -> court-circuit vers GND direct
        _out(_FAULT_PIN_Y0_GATE, GPIO.HIGH if GPIO_AVAILABLE else 1)
        print(f"  GPIO  : ISO ouvert (GPIO{_FAULT_PIN_ISO}=HIGH) | MUX Y0 (A=LOW,B=LOW) | Y0_GATE=HIGH")
        print(f"  INFO  : IRLZ44N sature -> Drain Y0 -> GND direct -> A0 ~0V")
        print(f"  [PROTECT] GPIO{_FAULT_PIN_ISO} ouvert AVANT GPIO{_FAULT_PIN_Y0_GATE}=HIGH : ADS1115 protege")

    elif mode == "SHORT TO VCC":
        # [PROTECT] GPIO18 HIGH EN PREMIER
        _ouvrir_iso()
        # MUX Y2 : A=LOW B=HIGH -> canal 2
        _out(_FAULT_PIN_MUX_A,   GPIO.LOW  if GPIO_AVAILABLE else 0)
        _out(_FAULT_PIN_MUX_B,   GPIO.HIGH if GPIO_AVAILABLE else 1)
        # [PROTECT] Source activee EN DERNIER : IRF4905 via 2N2222
        _out(_FAULT_PIN_Y2_BASE, GPIO.HIGH if GPIO_AVAILABLE else 1)
        print(f"  GPIO  : ISO ouvert (GPIO{_FAULT_PIN_ISO}=HIGH) | MUX Y2 (A=LOW,B=HIGH) | Y2_BASE=HIGH")
        print(f"  INFO  : IRF4905 -> pont diviseur 10k/10k -> A0 ~2.8V")

    elif mode == "VARIABLE LOAD":
        # [VLOAD] Sequence specifique :
        # ISO reste FERME -> ADS1115 lit noeud B directement (variation visible)
        # [PROTECT] Pas de risque sequencage ici : 100 Ohm serie sur drain Y3
        #           limite le courant meme si GPIO26 s'active avant ISO
        # MUX Y3 : A=HIGH B=HIGH -> canal 3 (cable reel : Drain IRLZ44N -> 100 Ohm -> Y3 pin 1)
        _out(_FAULT_PIN_MUX_A, GPIO.HIGH if GPIO_AVAILABLE else 1)
        _out(_FAULT_PIN_MUX_B, GPIO.HIGH if GPIO_AVAILABLE else 1)
        # ISO reste ferme (ne pas appeler _ouvrir_iso())
        # PWM active EN DERNIER -> IRLZ44N conduit -> charge variable sur Y3
        set_variable_load(duty)
        print(f"  GPIO  : ISO FERME (mesure directe) | MUX Y3 (A=HIGH,B=HIGH) | PWM={duty:.1f}%")
        print(f"  INFO  : ADS1115 lit variation sur noeud B via ISO ferme")
        print(f"  INFO  : Drain IRLZ44N -> [100 Ohm] -> Y3 -> MUX -> A0")
        print(f"  [NOTE] 100 Ohm serie : I_max ~10.8mA -> protege meme ISO ferme")

    print(f"  OK Mode '{mode}' applique sur POMPE")
    print(f"{'='*52}\n")
    return True


# ------------------------------------------------------------------------------
# MENU INTERACTIF
# ------------------------------------------------------------------------------
def _choisir(label: str, options: list) -> str:
    print(f"\n  {label} :")
    for i, opt in enumerate(options):
        print(f"    [{i+1}] {opt}")
    while True:
        try:
            choix = input(f"  Choix (1-{len(options)}) : ").strip()
            idx   = int(choix) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"  -> Entrer un numero entre 1 et {len(options)}")


def _choisir_duty() -> float:
    """[VLOAD] Demande le duty cycle a l'operateur."""
    while True:
        try:
            val = input("  Duty cycle (0.0 a 100.0) % : ").strip()
            f   = float(val)
            if 0.0 <= f <= 100.0:
                return f
        except (ValueError, KeyboardInterrupt):
            pass
        print("  -> Entrer une valeur entre 0.0 et 100.0")


def menu_interactif():
    mode_courant = "NORMAL"
    duty_courant = 50.0

    print("\n" + "="*52)
    print("  RPi SIMULATEUR - Controle Injection de Defauts")
    print("  Cible : POMPE (fixe)")
    print("="*52)
    print(f"  GPIO disponible : {'OUI' if GPIO_AVAILABLE else 'NON (simulation)'}")
    print(f"  [PROTECT] GPIO{_FAULT_PIN_ISO} (ISO) toujours premier HIGH / dernier LOW\n")

    while True:
        print(f"  Etat courant  ->  Mode: {mode_courant}"
              + (f"  |  Duty: {duty_courant:.1f}%" if mode_courant == "VARIABLE LOAD" else ""))
        print("\n  Actions :")
        print("    [1] Changer le mode")
        print("    [2] Appliquer mode actuel")
        print("    [3] Retour NORMAL rapide")
        if mode_courant == "VARIABLE LOAD":
            print("    [4] Ajuster duty cycle VARIABLE LOAD (en live)")
        print("    [0] Quitter\n")

        try:
            action = input("  Choix : ").strip()
        except KeyboardInterrupt:
            break

        if action == "0":
            break
        elif action == "1":
            mode_courant = _choisir("Selectionner le mode", MODES)
            print(f"  OK Mode selectionne : {mode_courant}")
            if mode_courant == "VARIABLE LOAD":
                duty_courant = _choisir_duty()
        elif action == "2":
            appliquer_mode(mode_courant, duty=duty_courant)
        elif action == "3":
            mode_courant = "NORMAL"
            appliquer_mode("NORMAL")
        elif action == "4" and mode_courant == "VARIABLE LOAD":
            duty_courant = _choisir_duty()
            set_variable_load(duty_courant)
            print(f"  OK Duty cycle mis a jour : {duty_courant:.1f}%")
        else:
            print("  -> Choix invalide")

    print("\n[SIM] Retour NORMAL complet avant fermeture...")
    _desactiver_sources()     # PWM -> 0%, tous signaux OFF
    _fermer_iso()             # [PROTECT] ISO ferme EN DERNIER
    if GPIO_AVAILABLE:
        if _pwm_vload is not None:
            _pwm_vload.stop()
        GPIO.cleanup()
    print("[SIM] Termine.")


# ------------------------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="RPi Simulateur - Injection de defauts GPIO (cible POMPE fixe)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  python3 sim_control.py\n"
            "  python3 sim_control.py --mode \"OPEN LOAD\"\n"
            "  python3 sim_control.py --mode \"SHORT TO VCC\"\n"
            "  python3 sim_control.py --mode \"SHORT TO GND\"\n"
            "  python3 sim_control.py --mode \"VARIABLE LOAD\" --duty 50\n"
            "  python3 sim_control.py --mode NORMAL\n"
            "  python3 sim_control.py --list\n"
        ),
    )
    p.add_argument("--mode",  choices=MODES, metavar="MODE",
                   help=f"Mode : {' | '.join(MODES)}")
    p.add_argument("--duty",  type=float, default=50.0, metavar="DUTY",
                   help="Duty cycle PWM pour VARIABLE LOAD (0.0 a 100.0, defaut 50.0)")
    p.add_argument("--list",  action="store_true",
                   help="Afficher les modes disponibles")
    args = p.parse_args()

    if args.list:
        print("Modes disponibles :", MODES)
        print("Cible             : POMPE (fixe)")
        sys.exit(0)

    if args.mode:
        ok = appliquer_mode(args.mode, duty=args.duty)
        if GPIO_AVAILABLE:
            if _pwm_vload is not None:
                _pwm_vload.stop()
            GPIO.cleanup()
        sys.exit(0 if ok else 1)

    menu_interactif()


if __name__ == "__main__":
    main()
