#!/usr/bin/env python3
"""
a2l_loader.py  —  Parseur A2L (ASAM MCD-2 MC) pour XCP WipeWash
================================================================
Module autonome sans dépendance externe.
Parse le subset A2L utilisé par le projet WipeWash (CHARACTERISTIC VALUE).

Expose une seule fonction publique :

    a2l_dict = load_a2l(path)

    Retourne un dict compatible avec le format attendu par xcp_slave.py
    et xcp_panel.py :

    {
        "PARAM_NAME": {
            "desc":     str,
            "unit":     str,
            "type":     "float" | "int",
            "default":  float | int,
            "min":      float | int,
            "max":      float | int,
            "step":     float | int,
            "category": str,        # ex. "TIMING", "PUMP", "PROTECTION"…
        },
        ...
    }

Champs lus dans les blocs ANNOTATION du fichier .a2l :
    ANNOTATION_LABEL "default"   → valeur par défaut (obligatoire)
    ANNOTATION_LABEL "category"  → catégorie UI (optionnel, défaut "TIMING")

Règles de typage :
    FLOAT32_IEEE / FLOAT64_IEEE / *FLOAT* layout → type "float"
    SWORD / UWORD / SLONG / ULONG / SBYTE / UBYTE layout → type "int"

Fallback :
    Si le fichier est absent ou mal formé, load_a2l() lève une exception
    claire. C'est à l'appelant (xcp_slave.py / xcp_master.py) de gérer
    le fallback éventuel.
"""

from __future__ import annotations

import re
import os
from typing import Any


# ══════════════════════════════════════════════════════════════════
#  Constantes
# ══════════════════════════════════════════════════════════════════

_FLOAT_LAYOUT_KEYWORDS = ("FLOAT32", "FLOAT64")
_DEFAULT_CATEGORY      = "TIMING"


# ══════════════════════════════════════════════════════════════════
#  Tokenizer A2L minimal
# ══════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> list[str]:
    """
    Retourne la liste des tokens A2L.
    Supprime les commentaires /* ... */ et // ... avant tokenisation.
    """
    # Supprimer commentaires bloc /* ... */
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    # Supprimer commentaires ligne // ...
    text = re.sub(r'//[^\n]*', ' ', text)

    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
            continue
        # Chaîne entre guillemets (peut être vide "")
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == '\\':
                    j += 1   # caractère échappé
                j += 1
            tokens.append(text[i:j + 1])
            i = j + 1
            continue
        # Mot ou nombre
        j = i
        while j < n and text[j] not in ' \t\r\n"':
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


# ══════════════════════════════════════════════════════════════════
#  Parser de blocs /begin … /end
# ══════════════════════════════════════════════════════════════════

class _Parser:
    def __init__(self, tokens: list[str]):
        self._tok = tokens
        self._pos = 0

    # ── Primitives ─────────────────────────────────────────────────

    def _peek(self) -> str | None:
        return self._tok[self._pos] if self._pos < len(self._tok) else None

    def _consume(self) -> str:
        t = self._tok[self._pos]
        self._pos += 1
        return t

    @staticmethod
    def _str_val(token: str) -> str:
        """Retire les guillemets d'une chaîne A2L."""
        if token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        return token

    @staticmethod
    def _num(token: str) -> float:
        """Parse un nombre A2L (hex 0x…, float ou int)."""
        t = token.strip()
        if t.startswith(('0x', '0X')):
            return float(int(t, 16))
        return float(t)

    def _skip_block(self, block_name: str):
        """Saute un bloc /begin BLOCK_NAME … /end BLOCK_NAME."""
        depth = 1
        while self._pos < len(self._tok) and depth > 0:
            t = self._consume()
            if t == '/begin':
                depth += 1
                self._consume()   # nom du sous-bloc
            elif t == '/end':
                depth -= 1
                self._consume()   # nom du /end

    # ── Parseur racine ─────────────────────────────────────────────

    def parse(self) -> dict[str, dict]:
        """
        Parcourt tous les tokens à la recherche de blocs CHARACTERISTIC
        et retourne le dict xcp-compatible.
        """
        result: dict[str, dict] = {}
        while self._pos < len(self._tok):
            t = self._peek()
            if t is None:
                break
            if t == '/begin':
                self._consume()
                block = self._consume()
                if block in ('PROJECT', 'MODULE'):
                    self._consume()   # nom (string) — on entre dans le bloc
                elif block == 'HEADER':
                    self._skip_block('HEADER')
                elif block == 'MOD_COMMON':
                    self._skip_block('MOD_COMMON')
                elif block == 'RECORD_LAYOUT':
                    self._skip_block('RECORD_LAYOUT')
                elif block == 'COMPU_METHOD':
                    self._skip_block('COMPU_METHOD')
                elif block == 'CHARACTERISTIC':
                    char = self._parse_characteristic()
                    if char:
                        name, meta = char
                        result[name] = meta
                else:
                    self._skip_block(block)
            elif t == '/end':
                self._consume()
                self._consume()   # nom du bloc fermant
            else:
                self._consume()   # token orphelin → ignorer
        return result

    # ── CHARACTERISTIC ─────────────────────────────────────────────

    def _parse_characteristic(self) -> tuple[str, dict] | None:
        """
        Parse un bloc CHARACTERISTIC après '/begin CHARACTERISTIC'.

        Grammaire ASAM A2L (subset VALUE) :
          name  description  type  address  record_layout
          max_diff  compu_method  lower_limit  upper_limit
          [ keyword / sous-bloc optionnels… ]
          /end CHARACTERISTIC
        """
        try:
            name      = self._consume()
            desc      = self._str_val(self._consume())
            _char_type = self._consume()           # VALUE / CURVE / MAP …
            _address  = self._consume()            # adresse (ignorée)
            layout    = self._consume()            # RECORD_LAYOUT name
            _max_diff = self._num(self._consume()) # max_diff (ignoré)
            _compu    = self._consume()            # COMPU_METHOD (ignoré)
            lower     = self._num(self._consume())
            upper     = self._num(self._consume())

            # Type Python déduit du nom du layout
            py_type = "float" if any(
                kw in layout.upper() for kw in _FLOAT_LAYOUT_KEYWORDS
            ) else "int"

            # Valeurs par défaut avant mots-clés optionnels
            unit     = ""
            step     = 1.0 if py_type == "float" else 1
            default  = lower   # fallback si pas d'ANNOTATION "default"
            category = _DEFAULT_CATEGORY

            # Lecture des tokens jusqu'au /end CHARACTERISTIC
            while True:
                t = self._peek()
                if t is None:
                    break
                if t == '/end':
                    self._consume()
                    end_name = self._consume()
                    if end_name == 'CHARACTERISTIC':
                        break
                    continue
                if t == '/begin':
                    self._consume()
                    sub = self._consume()
                    if sub == 'ANNOTATION':
                        label, val = self._parse_annotation()
                        if label == 'default':
                            try:
                                raw = val.strip()
                                if py_type == "float":
                                    default = float(raw)
                                else:
                                    default = int(float(raw))
                            except (ValueError, TypeError):
                                pass
                        elif label == 'category':
                            category = val.strip() or _DEFAULT_CATEGORY
                    elif sub == 'EXTENDED_LIMITS':
                        self._skip_block('EXTENDED_LIMITS')
                    elif sub == 'IF_DATA':
                        self._skip_block('IF_DATA')
                    else:
                        self._skip_block(sub)
                    continue

                # Mots-clés positionnels optionnels
                if t == 'STEP_SIZE':
                    self._consume()
                    raw_step = self._consume()
                    try:
                        if py_type == "float":
                            step = float(raw_step)
                        else:
                            step = int(float(raw_step))
                    except (ValueError, TypeError):
                        pass
                    continue
                if t == 'PHYS_UNIT':
                    self._consume()
                    unit = self._str_val(self._consume())
                    continue
                if t in ('FORMAT', 'DISPLAY_IDENTIFIER'):
                    self._consume()
                    self._consume()
                    continue
                if t in ('READ_ONLY', 'GUARD_RAILS'):
                    self._consume()
                    continue
                self._consume()   # token inconnu → ignorer

            # Ajustement des types entiers
            if py_type == "int":
                lower    = int(lower)
                upper    = int(upper)
                default  = int(default) if isinstance(default, float) else default
                step     = int(step)    if isinstance(step,    float) else step

            return name, {
                "desc":     desc,
                "unit":     unit,
                "type":     py_type,
                "default":  default,
                "min":      lower,
                "max":      upper,
                "step":     step,
                "category": category,
            }

        except Exception as e:
            print(f"[A2L-LOADER] Avertissement — CHARACTERISTIC ignoré: {e}")
            return None

    # ── ANNOTATION ─────────────────────────────────────────────────

    def _parse_annotation(self) -> tuple[str, str]:
        """
        Parse un bloc ANNOTATION après '/begin ANNOTATION'.
        Retourne (label, text_content).
        """
        label   = ""
        content = ""
        while True:
            t = self._peek()
            if t is None:
                break
            if t == '/end':
                self._consume()
                end_name = self._consume()
                if end_name == 'ANNOTATION':
                    break
                continue
            if t == 'ANNOTATION_LABEL':
                self._consume()
                label = self._str_val(self._consume())
                continue
            if t == 'ANNOTATION_ORIGIN':
                self._consume()
                self._consume()   # ignorer
                continue
            if t == '/begin':
                self._consume()
                sub = self._consume()
                if sub == 'ANNOTATION_TEXT':
                    parts = []
                    while True:
                        inner = self._peek()
                        if inner is None:
                            break
                        if inner == '/end':
                            self._consume()
                            end_inner = self._consume()
                            if end_inner == 'ANNOTATION_TEXT':
                                break
                            continue
                        parts.append(self._str_val(self._consume()))
                    content = " ".join(parts)
                else:
                    self._skip_block(sub)
                continue
            self._consume()
        return label, content


# ══════════════════════════════════════════════════════════════════
#  API publique
# ══════════════════════════════════════════════════════════════════

def load_a2l(path: str) -> dict[str, dict]:
    """
    Charge et parse un fichier A2L (ASAM MCD-2 MC).

    Paramètre :
        path : chemin vers le fichier .a2l

    Retourne :
        dict { param_name → { desc, unit, type, default, min, max,
                               step, category } }

    Lève :
        FileNotFoundError  si le fichier est absent
        ValueError         si le fichier est vide ou sans CHARACTERISTIC
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Fichier A2L introuvable : {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    if not text.strip():
        raise ValueError(f"Fichier A2L vide : {path}")

    tokens = _tokenize(text)
    result = _Parser(tokens).parse()

    if not result:
        raise ValueError(
            f"Aucun CHARACTERISTIC trouvé dans {path}. "
            "Vérifiez que le fichier A2L est bien formé."
        )

    print(f"[A2L-LOADER] {len(result)} paramètres chargés depuis {path}")
    return result


# ══════════════════════════════════════════════════════════════════
#  __main__ — test rapide en ligne de commande
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else "wiperwash_xcp.a2l"
    try:
        d = load_a2l(path)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        sys.exit(1)
class A2LLoader:
    """
    Public façade for the A2L parser, instantiable without arguments.
    Usage:
        loader = A2LLoader()
        result = loader.load("path/to/file.a2l")   # returns dict or None
    """

    def load(self, path: str) -> dict | None:
        """
        Charge et parse un fichier .a2l.
        Retourne le dict des CHARACTERISTIC, ou None si le fichier est
        absent / mal formé (pour ne pas bloquer les tests hors-BCM).
        """
        try:
            return load_a2l(path)
        except (FileNotFoundError, ValueError) as e:
            print(f"[A2LLoader] {e}")
            return None