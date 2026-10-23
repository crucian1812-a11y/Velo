#!/usr/bin/env python3
"""Латынь кириллицей, с ударением — для синтеза, который латиницу не читает.

    >>> latin_to_cyr("priori")
    'при+ори'

Правила — школьные правила чтения латыни, как её читают по-русски:
c перед e, i, y, ae, oe — «ц»; ti перед гласной — «ци»; s между гласными — «з»;
qu — «кв»; ae, oe — «э»; l перед согласной и в конце — «ль».

Ударение: в двусложном слове — на первый слог; в более длинном — на
предпоследний, если он долгий (дифтонг или закрыт двумя согласными),
иначе на третий от конца. Долготу гласных без макронов не видно, поэтому
для частых окончаний с долгим предпоследним слогом есть отдельный список.
"""

import re
import unicodedata

VOWELS = "aeiouy"
# Окончания, в которых предпоследний слог долгий: rati+ones, intuit+iva.
LONG_PENULT = ("ones", "onis", "onem", "oni", "atus", "ata", "atum", "ati",
               "atis", "atae", "atem", "ates", "ate", "alis", "ale", "ales", "arius", "aria", "arium",
               "ori", "oris", "orem", "ores", "orum", "erum",
               "osus", "osa", "osum", "ura", "urae", "inus", "ina", "inum", "ivus",
               "iva", "ivum", "ivi", "ivae", "arum", "atur", "are", "ire", "antur",
               "ensis", "ense")
MUTA = set("бпдтгкцф")
LIQUIDA = set("лр")


def _plain(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower())
                   if unicodedata.category(c) != "Mn")


def latin_to_cyr(word: str, stress: bool = True) -> str:
    w = _plain(word)
    n = len(w)
    units: list[list] = []          # [кириллица, гласная?, вес/тип]

    def at(k: int) -> str:
        return w[k] if 0 <= k < n else ""

    def prev_vowel() -> bool:
        return bool(units) and units[-1][1]

    i = 0
    while i < n:
        c, two = w[i], w[i:i + 2]
        if not c.isalpha():
            i += 1
            continue
        # Гласные и дифтонги.
        if two in ("ae", "oe"):
            units.append(["э" if not units else "е", True, 2])
            i += 2
            continue
        if two == "au":
            units.append(["ау", True, 2])
            i += 2
            continue
        if two == "eu" and i == 0:             # Euclides; в Deus и meus это два слога
            units.append(["эу", True, 2])
            i += 2
            continue
        if c in "ij" and (i == 0 or prev_vowel()) and at(i + 1) and at(i + 1) in VOWELS:
            units.append(["й", False, 1])   # согласное i: iustitia, maior
            i += 1
            continue
        if c in VOWELS:
            cyr = {"a": "а", "e": "е", "i": "и", "o": "о", "u": "у", "y": "и"}[c]
            if c == "e" and not units:
                cyr = "э"
            if units and units[-1][0] == "й":   # йа → я, йу → ю, йе → е
                merged = {"а": "я", "у": "ю", "е": "е", "э": "е"}.get(cyr)
                if merged:
                    units[-1] = [merged, True, 1]
                    i += 1
                    continue
            units.append([cyr, True, 1])
            i += 1
            continue
        # Согласные.
        if two == "qu":
            units.append(["кв", False, 1]); i += 2; continue
        if two == "gu" and at(i + 2) and at(i + 2) in VOWELS and units and units[-1][0] == "н":
            units.append(["гв", False, 1]); i += 2; continue
        if two == "ch":
            units.append(["х", False, 1]); i += 2; continue
        if two == "ph":
            units.append(["ф", False, 1]); i += 2; continue
        if two == "th":
            units.append(["т", False, 1]); i += 2; continue
        if two == "rh":
            units.append(["р", False, 1]); i += 2; continue
        if c == "c":
            soft = at(i + 1) in ("e", "i", "y") or w[i + 1:i + 3] in ("ae", "oe")
            units.append(["ц" if soft else "к", False, 1]); i += 1; continue
        if c == "t" and at(i + 1) == "i" and at(i + 2) and at(i + 2) in VOWELS \
                and at(i - 1) not in ("s", "t", "x"):
            units.append(["ц", False, 1]); i += 1; continue
        if c == "s" and prev_vowel() and at(i + 1) and at(i + 1) in VOWELS:
            units.append(["з", False, 1]); i += 1; continue
        if c == "x":
            units.append(["кс", False, 2]); i += 1; continue
        if c == "l":
            if at(i + 1) == "l":
                units.append(["лл", False, 2]); i += 2; continue
            nxt = at(i + 1)
            soft = (not nxt or (nxt.isalpha() and nxt not in VOWELS)) and prev_vowel()
            units.append(["ль" if soft else "л", False, 1]); i += 1; continue
        cyr = {"b": "б", "d": "д", "f": "ф", "g": "г", "h": "х", "k": "к", "m": "м",
               "n": "н", "p": "п", "r": "р", "s": "с", "t": "т", "v": "в", "w": "в",
               "z": "з"}.get(c, "")
        units.append([cyr, False, 1])
        i += 1

    nuclei = [k for k, u in enumerate(units) if u[1]]
    target = None
    if stress and len(nuclei) >= 2:
        if len(nuclei) == 2:
            target = nuclei[0]
        else:
            pen, last = nuclei[-2], nuclei[-1]
            between = units[pen + 1:last]
            weight = sum(u[2] for u in between)
            if len(between) == 2 and between[0][0] in MUTA and between[1][0] in LIQUIDA:
                weight = 1                      # muta cum liquida не удлиняет слог
            heavy = units[pen][2] == 2 or weight >= 2 or w.endswith(LONG_PENULT)
            target = pen if heavy else nuclei[-3]
    out = []
    for k, u in enumerate(units):
        out.append(("+" if k == target else "") + u[0])
    return "".join(out)


if __name__ == "__main__":
    import sys
    for word in sys.argv[1:]:
        print(word, "→", latin_to_cyr(word))
