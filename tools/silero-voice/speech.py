#!/usr/bin/env python3
"""Подготовка очищенного текста к синтезу речи.

    python3 scripts/speech.py text/.../clean.txt > speech.txt
    python3 scripts/speech.py --report                  # что осталось по всей библиотеке

Silero не читает цифры — их надо развернуть словами, и в правильном падеже:
«в 504 году» — это «в пятьсот четвёртом году», а не «в пятьсот четыре году».
Здесь же выкидываются номера сносок, которые распознавание оставило цифрами
после слова, чинится испорченное «н. э.» и расставляются ударения в именах.

Очищенный текст главы не трогается: это текст для чтения глазами, а здесь
готовится текст для уха.
"""

import argparse
import pathlib
import re
import sys

from num2words import lang_RU

from latin import latin_to_cyr

RU = lang_RU.Num2Word_RU()
CYR = "А-Яа-яЁё"

# «н. э.», которое распознавание прочитало как «н. з.», «н. 9.», «н. 2.»,
# «н. a.», «u. э.», «ин. 9.», «дон. 3.». Настоящих «н. 9.» в этих книгах нет.
NE = re.compile(rf"(?<![{CYR}])(до\s*)?и?[нu]\.\s?[эЭзЗ392aа](?![{CYR}\d])\.?")

# Имена, которые распознавание стабильно портит: «Ж» вместо «Х», «з» вместо «э».
OCR_FIX = [("Бозци", "Боэци"), ("Аверрозс", "Аверроэс"), ("Жайдеггер", "Хайдеггер"),
           ("Къеркегор", "Кьеркегор"), ("Жрисипп", "Хрисипп"), ("Жрист", "Христ"),
           ("Геэтет", "Теэтет"), ("Гимее", "Тимее"), ("Гимей", "Тимей")]

ABBREVIATIONS = [
    (r"\(лат\.\s*\)", "(по-латыни)"),
    (r"\(греч\.\s*\)", "(по-гречески)"),
    (r"\(нем\.\s*\)", "(по-немецки)"),
    (r"\(фр\.\s*\)", "(по-французски)"),
    (r"\(англ\.\s*\)", "(по-английски)"),
    (r"\bдо\s+н\.\s*э\.", "до нашей эры"),
    (r"\bн\.\s*э\.", "нашей эры"),
    (r"(?<![{0}])ок\.(?=\s*\d)".format(CYR), "около"),
    (r"(?<![{0}])ум\.(?=\s)".format(CYR), "умер"),
    (r"(?<![{0}])род\.(?=\s*(?:ок|в|\d))".format(CYR), "родился"),
    (r"\bи\s+т\.\s*д\.", "и так далее"),
    (r"\bи\s+т\.\s*п\.", "и тому подобное"),
    (r"\bт\.\s*е\.", "то есть"),
    (r"\bи\s+др\.", "и другие"),
    (r"\bср\.", "сравни"),
    (r"\bсм\.", "смотри"),
]

# Предлог перед годом решает падеж, когда слова «год» рядом нет.
PREP_CASE = {
    "в": "p", "во": "p", "на": "p", "о": "p", "об": "p", "при": "p",
    "к": "d", "ко": "d",
    "около": "g", "до": "g", "после": "g", "с": "g", "со": "g", "от": "g",
    "из": "g", "начала": "g", "начале": "g", "середины": "g", "середине": "g",
    "конца": "g", "конце": "g", "течение": "g", "весны": "g", "весной": "g",
    "осени": "g", "осенью": "g", "летом": "g", "зимой": "g", "зимы": "g",
    "лета": "g", "без": "g", "для": "g", "кроме": "g", "среди": "g", "у": "g",
    "между": "i", "за": "i", "перед": "i", "под": "i", "над": "i",
    "по": "a", "через": "a", "про": "a",
    "умер": "p", "родился": "p",
}

# Форма слова «год» → падеж порядкового числительного перед ним.
YEAR_WORD = {
    "год": "n", "года": "g", "году": "p", "годом": "i", "годе": "p",
    "годы": "n", "годов": "g", "годам": "d", "годах": "p", "годами": "i",
}
# Какую форму «года» вставить, если в тексте было «г.» или слова не было вовсе.
YEAR_FORM = {"n": "год", "a": "год", "g": "года", "d": "году", "p": "году", "i": "годом"}
YEARS_FORM = {"n": "годы", "a": "годы", "g": "годов", "d": "годам", "p": "годах", "i": "годами"}

CENTURY = {"век": "n", "века": "g", "веку": "d", "веке": "p", "веком": "i",
           "веков": "g", "веках": "p", "веками": "i"}

# Слова, после которых число — это номер, а не сноска: «книги 5», «Псалтирь 23».
NUMBERED_STEMS = ("глав", "гл", "книг", "кн", "том", "част", "стр", "псалт", "псалм",
                  "фрагмент", "фр", "параграф", "письм", "послани", "номер", "пункт",
                  "тезис", "правил", "аксиом", "положени", "вопрос", "стать", "раздел",
                  "стих", "песн", "диалог")


def is_numbered(word: str) -> bool:
    return any(word.startswith(stem) for stem in NUMBERED_STEMS)


# Окончание после дефиса у порядкового: «69-й», «5-го», «3-м».
SUFFIX_CASE = {"й": ("n", "m"), "ый": ("n", "m"), "ой": ("n", "m"), "ий": ("n", "m"),
               "я": ("n", "f"), "ая": ("n", "f"), "е": ("n", "n"), "ое": ("n", "n"),
               "го": ("g", "m"), "ого": ("g", "m"), "его": ("g", "m"),
               "му": ("d", "m"), "ому": ("d", "m"), "ему": ("d", "m"),
               "ю": ("a", "f"), "ую": ("a", "f"),
               "х": ("g", "m"), "ых": ("g", "m"), "их": ("g", "m"),
               "ми": ("i", "m"), "ыми": ("i", "m")}


def ordinal(n: int, case: str, gender: str = "m", plural: bool = False) -> str:
    if case == "a":                     # неодушевлённый винительный = именительный
        case = "n"
    return RU.to_ordinal(n, case=case, gender=gender, plural=plural)


def cardinal(n: int, case: str = "n", gender: str = "m") -> str:
    if case == "a":
        case = "n"
    return RU.to_cardinal(n, case=case, gender=gender)


def after_link(text: str, pos: int) -> bool:
    """Число стоит сразу после «и», «или» или тире — оно в падеже соседа слева."""
    return bool(re.search(r"(?:\s(?:и|или|либо)|[—–-])\s*$", text[max(0, pos - 8):pos]))


def prev_word(text: str, pos: int) -> str:
    words = re.findall(rf"[{CYR}]+", text[max(0, pos - 30):pos])
    return words[-1].lower() if words else ""


def next_word(text: str, pos: int) -> str:
    m = re.match(rf"\s*([{CYR}]+)", text[pos:pos + 30])
    return m.group(1).lower() if m else ""


def fix_era(text: str) -> str:
    text = re.sub(rf"(?<=[{CYR}] )0[7т](?= [{CYR}])", "от", text)
    text = NE.sub(lambda m: "до н. э." if m.group(1) else "н. э.", text)
    for pattern, repl in ABBREVIATIONS:
        text = re.sub(pattern, repl, text)
    return text


def drop_footnotes(text: str) -> str:
    """Номер сноски после слова перед знаком препинания: «добродетели 2.»"""
    def repl(m: re.Match) -> str:
        word = prev_word(text, m.start())
        if is_numbered(word) or word in PREP_CASE:
            return m.group(0)
        return ""
    return re.sub(rf"(?<=[{CYR}»\")\]])\s+\d{{1,3}}(?=[.,;:!?)»]|\s*$)", repl, text,
                  flags=re.M)


def expand_numbers(text: str) -> str:
    # Ссылки на страницы: «с. 12—14», «стр. 45».
    text = re.sub(rf"(?<![{CYR}])(?:с|стр)\.\s*(?=\d+\s*[—–-]\s*\d)", "страницы ", text)
    text = re.sub(rf"(?<![{CYR}])(?:с|стр)\.\s*(?=\d)", "страница ", text)

    # Номер раздела в начале абзаца: «3. Когда схолархом…» — «Третье. Когда…»
    text = re.sub(r"(?m)^(\d{1,2})\.\s+(?=[А-ЯЁ])",
                  lambda m: ordinal(int(m.group(1)), "n", "n").capitalize() + ". ", text)

    # «Глава 4» — женский род, именительный.
    text = re.sub(r"\b(Глава|глава)\s+(\d{1,3})\b",
                  lambda m: f"{m.group(1)} {ordinal(int(m.group(2)), 'n', 'f')}", text)

    # Порядковые с окончанием через дефис: «69-й», «с 5-го по», «в 60-х годах».
    def suffixed(m: re.Match) -> str:
        n, suf = int(m.group(1)), m.group(2)
        src = text_ref[0]
        prep = prev_word(src, m.start())
        linked = after_link(src, m.start()) and last["suffixed"]
        nxt = next_word(src, m.end())
        plural = False
        case, gender = SUFFIX_CASE.get(suf, ("n", "m"))
        if suf in ("м", "ом", "ем"):                # предложный или творительный
            case = "i" if prep in ("между", "за", "перед", "под", "над", "с", "со") else "p"
        elif suf == "ым":
            case = "i"
        elif suf in ("х", "ых", "их"):               # «в 60-х» — предложный, «с 60-х» — родительный
            plural = True
            case = "p" if prep in ("в", "во", "о", "об", "на", "при") else "g"
        elif suf in ("ми", "ыми"):
            plural = True
        elif suf in ("е", "ое") and re.search(r"[ыи]$", nxt):
            plural = True                           # «60-е годы»
        elif suf == "й" and nxt and not re.search(r"[бвгджзйклмнпрстфхцчшщь]$", nxt):
            case, gender = "g", "f"                 # «69-й Олимпиады»: женский косвенный
        if linked:
            case, gender, plural = last["suffixed"]
        last["suffixed"] = (case, gender, plural)
        return ordinal(n, case, gender, plural)
    last = {"suffixed": None, "year": None}
    text_ref = [text]
    text = re.sub(rf"\b(\d{{1,4}})-(й|ый|ой|ий|я|ая|е|ое|го|ого|его|му|ому|ему|м|ым|ом|ем|ю|ую|х|ых|их|ми|ыми)(?![{CYR}])",
                  suffixed, text)

    # Даты: «к 27 марта» — «к двадцать седьмому марта». Без предлога — родительный:
    # «27 марта он…» по-русски говорят «двадцать седьмого марта».
    months = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
    def date(m: re.Match) -> str:
        prep = prev_word(text_d[0], m.start())
        case = {"к": "d", "ко": "d", "по": "a", "на": "a"}.get(prep, "g")
        return f"{ordinal(int(m.group(1)), case, 'n')} {m.group(2)}"
    text_d = [text]
    text = re.sub(rf"\b(\d{{1,2}})\s+({months})\b", date, text)

    # Век арабскими: «в 5 веке».
    text = re.sub(r"\b(\d{1,2})\s+(век[а-я]*)\b",
                  lambda m: f"{ordinal(int(m.group(1)), CENTURY.get(m.group(2).lower(), 'n'))} {m.group(2)}",
                  text)

    # Годы и диапазоны лет. Падеж — от слова «год», а если его нет — от предлога.
    year_re = re.compile(
        r"(?<![\d,.])(\d{1,4})(?!\d)(?:\s*[—–-]\s*(\d{1,4})(?!\d))?"
        r"(\s*(?:(год[а-я]*)|(гг?\.)))?"
        r"(?=(\s*(?:до\s+)?н(?:ашей)?\.?\s*э(?:ры)?\.?)?)")

    def year(m: re.Match) -> str:
        a, b = int(m.group(1)), m.group(2)
        word, abbr, era = m.group(4), m.group(5), m.group(6)
        prep = prev_word(text_y[0], m.start())
        if not word and not abbr and re.match(r"\s*[ab](?![A-Za-zА-Яа-яЁё])", text_y[0][m.end():m.end() + 3]):
            return m.group(0)               # страница по Беккеру: «1072 b»
        is_year = bool(word or abbr or era)
        if not is_year and len(m.group(1)) == 4 and 1000 <= a <= 2100 and not b:
            # Одинокое четырёхзначное число — почти всегда год: «Лондон, 1955».
            is_year = not re.match(rf"\s*[{CYR}]", text_y[0][m.end():m.end() + 3])
        dash_year = re.match(r"\s*[—–-]\s*(?:около|до|после|\d)", text_y[0][m.end():m.end() + 12])
        if not is_year and not b and dash_year and len(m.group(1)) >= 2:
            is_year = True                  # «Гален (129 — ок. 199 н. э.)»
        if not is_year and prep in PREP_CASE and 100 <= a <= 2100 and len(m.group(1)) >= 3:
            follow = text_y[0][m.end():m.end() + 3]
            is_year = (not re.match(rf"\s*[{CYR}]", follow)
                       or bool(re.match(r"\s*(?:или|и|либо|по|до)\s+\d{3,4}", text_y[0][m.end():m.end() + 12])))
        if not is_year:
            return m.group(0)
        if word:
            case = YEAR_WORD.get(word.lower(), "n")
            if word.lower() == "году" and prep in ("к", "ко", "по", "благодаря"):
                case = "d"
            tail = " " + word
        else:
            case = PREP_CASE.get(prep, "n")
            if after_link(text_y[0], m.start()) and last["year"]:
                case = last["year"]         # «около 20 г. до н. э. — 40 г. н. э.»
            plural = bool(abbr == "гг.")
            follow_word = next_word(text_y[0], m.end())
            # «в 1265 или 1266 году»: слово «год» стоит после второго числа.
            if dash_year and not b and not abbr:
                tail = ""                   # «год» прозвучит после второго числа
            elif follow_word in ("или", "и", "либо", "по", "до") and re.match(
                    r"\s*(?:или|и|либо|по|до)\s+\d{3,4}\s*(?:год|гг?\.)", text_y[0][m.end():m.end() + 25]):
                tail = ""
            else:
                tail = " " + (YEARS_FORM if plural or b else YEAR_FORM)[case]
                if b and not abbr:
                    tail = " " + YEAR_FORM[case]    # «около 440—430 до н. э.» — «года»
        last["year"] = case
        out = ordinal(a, case)
        if b:
            out += " — " + ordinal(int(b), case)
        return out + tail
    text_y = [text]
    text = year_re.sub(year, text)

    # Всё остальное — количественные. Падеж — от предлога.
    def plain(m: re.Match) -> str:
        raw = m.group(0)
        src = text_p[0]
        before, after = src[max(0, m.start() - 1):m.start()], src[m.end():m.end() + 1]
        # Мусор распознавания: цифры, прилипшие к буквам («1Шампо», «4a»),
        # после «#» и «=» (так ломается греческий), с ведущим нулём.
        if re.match(r"[A-Za-zА-Яа-яЁё#=]", before) or re.match(r"[A-Za-zА-Яа-яЁё]", after) \
                or (len(raw) > 1 and raw.startswith("0") and "," not in raw and "." not in raw):
            return ""
        if "," in raw or "." in raw:
            return RU.to_cardinal(float(raw.replace(",", ".")))
        n = int(raw)
        case = PREP_CASE.get(prev_word(text_p[0], m.start()), "n")
        if case in ("p", "a", "i"):          # «в 3 книгах», «по 5» — не угадать, берём именительный
            case = "n"
        nxt = next_word(text_p[0], m.end())
        gender = "f" if n % 10 in (1, 2) and n % 100 not in (11, 12) and re.search(r"[ая]$", nxt) else "m"
        return re.sub(r"^одн[а-я]* (тысяч)", r"\1", cardinal(n, case, gender))
    text_p = [text]
    text = re.sub(r"(?<![\d])\d+(?:[.,]\d+)?(?![\d])", plain, text)
    return text


# ─── Латиница ────────────────────────────────────────────────────────────────

LAT = "A-Za-zÀ-ÿ"
# Латинские буквы, которые распознавание подставляет вместо кириллических.
LOOKALIKE = str.maketrans("aceopxykuABCEHKMOPTXY", "асеорхукиАВСЕНКМОРТХУ")
LOOKALIKE_SET = set("aceopxykuABCEHKMOPTXY")
# Цифры, прилипшие к буквам, — тоже подмена: «ce6e» — «себе», «3a» — «за».
DIGIT_LOOK = str.maketrans("603", "боз")

LETTER_NAME = {"a": "а", "b": "бэ", "c": "цэ", "d": "дэ", "e": "е", "f": "эф", "g": "гэ",
               "h": "аш", "i": "и", "j": "йот", "k": "ка", "l": "эль", "m": "эм", "n": "эн",
               "o": "о", "p": "пэ", "q": "ку", "r": "эр", "s": "эс", "t": "тэ", "u": "у",
               "v": "вэ", "w": "дубль-вэ", "x": "икс", "y": "игрек", "z": "зэт"}

ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
# Римские цифры, распознанные кириллицей: «ХШ», «ХИ», «ХПИ». В средневековом
# томе это почти всегда тринадцатый век — Луллий, расцвет университетов.
ROMAN_LOOK = [("ХПИ", "XIII"), ("ХИ", "XIII"), ("Ш", "III"), ("П", "II"), ("Х", "X"),
              ("У", "V"), ("І", "I"), ("!", "I"), ("l", "I"), ("1", "I"), ("]", "")]
CENTURY_NOUN = {"век": ("n", "m"), "века": ("g", "m"), "веку": ("d", "m"), "веке": ("p", "m"),
                "веком": ("i", "m"), "веков": ("g", "m"), "веках": ("p", "m"), "веками": ("i", "m"),
                "столетие": ("n", "n"), "столетия": ("g", "n"), "столетию": ("d", "n"),
                "столетии": ("p", "n"), "столетием": ("i", "n"), "столетий": ("g", "n"),
                "столетиях": ("p", "n"), "столетиями": ("i", "n")}
STARTERS = {"если", "это", "так", "тогда", "то", "но", "однако", "итак", "пусть",
            "где", "когда", "здесь", "там", "все", "всё", "как"}
AROUND_CENTURY = {"конец", "конца", "конце", "начало", "начала", "начале", "середина",
                  "середины", "середине", "рубеж", "рубежа", "рубеже"}


def roman_value(s: str) -> int | None:
    if not re.fullmatch(r"C{0,3}(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", s) or not s:
        return None
    total = prev = 0
    for ch in reversed(s):
        v = ROMAN_VAL[ch]
        total, prev = (total - v, prev) if v < prev else (total + v, v)
    return total or None


def normalize_roman(tok: str) -> str:
    for a, b in ROMAN_LOOK:
        tok = tok.replace(a, b)
    return tok


def fix_glued_digits(text: str) -> str:
    return re.sub(rf"(?<=[{LAT}{CYR}])[603]|[603](?=[{LAT}{CYR}])",
                  lambda m: m.group(0).translate(DIGIT_LOOK), text)


def expand_roman(text: str) -> str:
    # Пункты списков: «i)», «ii)», «Ш)» — «первое», «второе», «третье».
    def item(m: re.Match) -> str:
        v = roman_value(normalize_roman(m.group(1).upper()))
        return f"{ordinal(v, 'n', 'n')}," if v else m.group(0)
    text = re.sub(rf"(?<![{LAT}{CYR}])(i{{1,3}}|iv|vi{{0,3}}|ix|x|Ш)\)", item, text)
    # Буквенные пункты: «а)», «b)» — «а», «бэ».
    names = {"а": "а", "a": "а", "б": "бэ", "b": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е"}
    text = re.sub(rf"(?<![{LAT}{CYR}])([абвгдеab])\)(?=\s)", lambda m: names[m.group(1)] + ",", text)

    cand = re.compile(rf"(?<![{LAT}{CYR}])([IVXLC][IVXLCХШПИУІ!l1\]]*|Х[ШПИУІXIV!l1\]]+)(?![{LAT}{CYR}])")

    def one(m: re.Match) -> str:
        raw = m.group(1)
        v = roman_value(normalize_roman(raw))
        if not v:
            return raw
        src = text_r[0]
        after = src[m.end():m.end() + 40]
        # Цепочка «XIII и XIV веках», «XVI — начало …»: ищем «век» за цепочкой.
        chain = re.match(rf"((?:\s*(?:и|или|—|–|-)\s*[IVXLCХШПИУ!l1\]]+)*)\s*([{CYR}]+)", after)
        noun = chain.group(2).lower() if chain else ""
        prev = prev_word(src, m.start())
        if noun in CENTURY_NOUN:
            case, gender = CENTURY_NOUN[noun]
            return ordinal(v, case, gender)
        if prev in AROUND_CENTURY or (prev in PREP_CASE and len(raw) >= 2 and not raw.startswith("Х")):
            case = "g" if prev in AROUND_CENTURY else PREP_CASE.get(prev, "n")
            return ordinal(v, case)
        before = src[max(0, m.start() - 30):m.start()]
        name = re.search(rf"(?<![.!?…]\s)([А-ЯЁ][{CYR}]{{2,}})\s*$", before)
        window = src[max(0, m.start() - 200):m.end() + 200]
        if len(raw) == 1 and re.search(r"\b[A-Z]\b\s*(?:[,;»)]|и\s+[A-Z]\b|то\s+[A-Z]\b)", window):
            return raw                      # «Если X, то Y» — переменная, не число
        if name and name.group(1).lower() not in STARTERS:   # «Иннокентию II», «Львом XIII»
            w = name.group(1).lower()
            case = ("d" if w.endswith(("у", "ю")) else "i" if w.endswith(("ом", "ем", "ём"))
                    else "g" if w.endswith(("а", "я")) else "p" if w.endswith("е") else "n")
            return ordinal(v, case)
        if raw.startswith("Х"):              # кириллицу без контекста не трогаем
            return raw
        if is_numbered(prev):               # «книга X», «том II»
            fem = prev.startswith(("книг", "глав", "част", "стать"))
            nom = prev.endswith(("а", "я", "ь")) or prev in ("том", "раздел")
            return ordinal(v, "n" if nom else "g", "f" if fem else "m")
        if len(raw) >= 2:
            return ordinal(v, "n")
        return raw
    text_r = [text]
    return cand.sub(one, text)


def load_foreign(path: pathlib.Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                k, v = line.split("\t")
                out[k.lower()] = v
    return out


def fix_latin(text: str, foreign: dict[str, str]) -> str:
    """Латиницу — в кириллицу: подмены вернуть, латынь переписать, мусор убрать."""
    out = []
    for para in text.split("\n\n"):
        # Абзац с логикой: «если X, то Y» — здесь буквы это переменные.
        logic = bool(re.search(rf"(?<![{LAT}{CYR}])[A-Z](?![{LAT}{CYR}])\s*(?:[,;»)]|и\s+[A-Z]\b|то\s+[A-Z]\b)", para))
        if logic:
            para = re.sub(rf"(?<![{LAT}{CYR}])Х(?![{LAT}{CYR}])", "икс", para)
        tokens = re.split(rf"([{LAT}{CYR}]+)", para)
        words = [t for t in tokens if t and re.match(rf"[{LAT}{CYR}]", t)]

        def is_real_latin(w: str) -> bool:
            return bool(re.fullmatch(rf"[{LAT}]+", w)) and len(w) >= 2 and not set(w) <= LOOKALIKE_SET \
                and not re.search(r"[a-z][A-Z]", w)

        for k, tok in enumerate(tokens):
            if not tok or not re.search(rf"[{LAT}]", tok):
                continue
            if re.search(rf"[{CYR}]", tok):                  # «выBOAHAO», «eловека»
                tok = tok.translate(LOOKALIKE)
                tokens[k] = re.sub(rf"[{LAT}]", "", tok)
                continue
            low = tok.lower()
            if low in foreign:
                tokens[k] = foreign[low]
                continue
            neighbours = [t for t in (tokens[k - 2] if k >= 2 else "", tokens[k + 2] if k + 2 < len(tokens) else "")]
            in_phrase = any(is_real_latin(t) for t in neighbours)
            if len(tok) == 1:
                if logic and (tok.isupper() or tok in "pqxy"):
                    tokens[k] = LETTER_NAME.get(low, tok)
                elif in_phrase and tok in "ae":
                    tokens[k] = latin_to_cyr(tok, stress=False)   # «a priori», «e contrario»
                elif tok in LOOKALIKE_SET:
                    tokens[k] = tok.translate(LOOKALIKE)
                else:
                    tokens[k] = LETTER_NAME.get(low, "")
                continue
            if set(tok) <= LOOKALIKE_SET and not in_phrase and low not in ("ex", "ea", "eo", "ac"):
                tokens[k] = tok.translate(LOOKALIKE)       # «HACH», «Tax»
                continue
            if re.search(r"[a-z][A-Z]", tok) or (tok.isupper() and len(tok) > 1) \
                    or re.search(r"[À-ÿ]", tok):
                tokens[k] = ""                             # «EyeELv», «TOADKO» — обломки греческого
                continue
            tokens[k] = latin_to_cyr(tok)
        out.append("".join(tokens))
    return "\n\n".join(out)


# После этих слов «году» — дательный: «к тысяча двести семьдесят седьмому
# г+оду». Во всех остальных случаях — предложный: «в том же год+у».
DATIVE_BEFORE = {"к", "ко", "по", "благодаря", "согласно", "навстречу"}


def stress_year(text: str) -> str:
    def repl(m: re.Match) -> str:
        prev = prev_word(text_g[0], m.start())
        if prev in DATIVE_BEFORE or prev.endswith(("ому", "ему")):
            return m.group(0)
        return m.group(1) + "од+у"
    text_g = [text]
    return re.sub(rf"(?<![{CYR}+])([Гг])оду(?![{CYR}+])", repl, text)


def load_words(path: pathlib.Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                k, v = line.split("\t")
                out[k.lower()] = v
    return out


def stress_words(text: str, words: dict[str, str]) -> str:
    """Точные словоформы из audio/words.tsv; регистр первой буквы сохраняется."""
    if not words:
        return text
    pattern = re.compile(rf"(?<![{CYR}+])({'|'.join(map(re.escape, sorted(words, key=len, reverse=True)))})(?![{CYR}+])",
                         re.IGNORECASE)

    def repl(m: re.Match) -> str:
        w = m.group(1)
        marked = words[w.lower()]
        return marked[0].upper() + marked[1:] if w[0].isupper() else marked
    return pattern.sub(repl, text)


def load_stress(path: pathlib.Path) -> list[tuple[re.Pattern, str]]:
    rules = []
    if not path.exists():
        return rules
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        stem, marked = line.split("\t")
        # Основа плюс падежное окончание до трёх букв. Длиннее — уже
        # производное слово, и ударение в нём часто другое.
        rules.append((re.compile(rf"(?<![{CYR}+]){re.escape(stem)}(?=[а-яё]{{0,3}}(?![{CYR}+]))"),
                      marked))
    # Длинные основы раньше коротких: «Юстиниан» размечается целиком,
    # и «Юстин» в нём уже не находится.
    rules.sort(key=lambda r: -len(r[1]))
    return rules


def put_stress(text: str, rules) -> str:
    for pattern, marked in rules:
        text = pattern.sub(marked, text)
    return text


def prepare(text: str, stress: pathlib.Path | None = pathlib.Path("audio/stress.tsv"),
            foreign: pathlib.Path = pathlib.Path("audio/foreign.tsv")) -> str:
    # «+» для Silero — знак ударения. Случайные плюсы из распознавания
    # его ломают: «+(» роняет синтез. Свои плюсы ставим только мы.
    text = text.replace("+", " ")
    for wrong, right in OCR_FIX:
        text = text.replace(wrong, right)
    text = re.sub(r"\*+", "", text)            # звёздочки сносок
    text = re.sub(r"[\^|~_#{}<>]", " ", text)   # мусор распознавания; «^» у Silero служебный
    text = fix_era(text)
    text = fix_glued_digits(text)
    text = drop_footnotes(text)
    text = expand_roman(text)
    text = expand_numbers(text)
    text = fix_latin(text, load_foreign(foreign))
    # Скобки для уха — это пауза. Silero к тому же падает на скобке рядом
    # со словом, в котором стоит ударение: «(н+оус)».
    text = re.sub(r"\s*[(\[]\s*", ", ", text)
    text = re.sub(r"\s*[)\]]", ",", text)
    text = re.sub(r",\s*([.,;:!?—])", r"\1", text)
    text = re.sub(r"([.!?;:—«])\s*,", r"\1", text)
    text = re.sub(r"(?m)^\s*,\s*", "", text)
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if stress:
        text = put_stress(text, load_stress(stress))
        text = stress_words(text, load_words(stress.parent / "words.tsv"))
        text = stress_year(text)
    # Ударение бывает только перед гласной — всё прочее Silero не переварит.
    text = re.sub(r"\+(?![аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "", text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?")
    ap.add_argument("--no-stress", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="прогнать всю библиотеку и показать, сколько цифр осталось")
    args = ap.parse_args()
    stress = None if args.no_stress else pathlib.Path("audio/stress.tsv")

    if args.report:
        left = total = 0
        for f in sorted(pathlib.Path("text").rglob("ocr/ch*/clean.txt")):
            src = f.read_text(encoding="utf-8")
            total += len(re.findall(r"\d+", src))
            out = prepare(src, None)
            rest = re.findall(r"\d+", out)
            left += len(rest)
            if rest:
                print(f"{f.parts[1]} {f.parent.name}: осталось {len(rest)}")
        print(f"чисел было {total}, осталось {left}")
        return

    if not args.text:
        ap.error("нужен файл или --report")
    sys.stdout.write(prepare(pathlib.Path(args.text).read_text(encoding="utf-8"), stress))


if __name__ == "__main__":
    main()
