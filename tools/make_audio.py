#!/usr/bin/env python3
"""Озвучка приложения голосом Silero: рассказы экскурсии, ответы викторины и подсказки.

    python tools/make_audio.py                    # голос xenia → audio/xenia/, js/voice.js
    python tools/make_audio.py --speaker baya     # другой голос
    python tools/make_audio.py --dry-run          # только тексты для проверки → audio/<голос>/texts.txt

Нужны: node (чтобы прочитать js/pois.js), ffmpeg, Python-пакеты torch, numpy, num2words
(см. tools/silero-voice/README.md). Модель Silero скачивается при первом запуске.
Готовые mp3 и js/voice.js коммитятся в репозиторий — приложение играет их, а системный
синтезатор речи остаётся запасным вариантом.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "tools" / "silero-voice"
sys.path.insert(0, str(VOICE_DIR))

from num2words import num2words  # noqa: E402
from speech import prepare  # noqa: E402

DIRECTIONS = ["севере", "северо-востоке", "востоке", "юго-востоке", "юге", "юго-западе", "западе", "северо-западе"]


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def load_pois() -> list[dict]:
    js = ("const vm=require('vm'),fs=require('fs');const w={};"
          "vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window:w});"
          "process.stdout.write(JSON.stringify(w.POIS));")
    out = subprocess.run(["node", "-e", js, str(ROOT / "js" / "pois.js")],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# Обороты, на которых подготовка текста ошибается с падежом или родом, — пишем словами
FIXES = {
    "ВЕЛО1": "Вело-один",
    "GPX": "джи-пи-икс",
    "более 1000 км": "более тысячи километров",
    "22 км": "двадцать два километра",
    "1-й ударной": "первой ударной",
    "45–60 минут": "сорок пять — шестьдесят минут",
    "112": "сто двенадцать",
}


def words(n: int, feminine: bool = False) -> str:
    """Число словами в именительном падеже; feminine — «одну», «две» (за одну минуту)."""
    w = num2words(n, lang="ru")
    if feminine:
        w = re.sub(r"\bодин$", "одну", w)
        w = re.sub(r"\bдва$", "две", w)
    return w


def for_ear(text: str) -> str:
    """Сокращения, которые модель не прочитает сама."""
    for a, b in FIXES.items():
        text = text.replace(a, b)
    # После «около», «до», «более» числительное в родительном — и «километров»
    text = re.sub(r"\b(около|до|более|свыше|от)\s+(\d+)\s*км\b", r"\1 \2 километров", text)
    text = re.sub(r"(\d+)\s*км\b", lambda m: f"{m.group(1)} " + plural(int(m.group(1)), "километр", "километра", "километров"), text)
    text = re.sub(r"№\s?(\d+)", r"номер \1", text)
    return text


def phrases(pois: list[dict]) -> dict[str, str]:
    """Ключ → текст. Ключи должны совпадать с теми, что спрашивает js/app.js."""
    p: dict[str, str] = {}
    for poi in pois:
        title = poi["title"] if poi["title"][-1] in ".!?" else poi["title"] + "."
        text = title + "\n\n" + "\n\n".join(poi["story"])
        if poi.get("quiz"):
            text += "\n\nВопрос викторины: " + poi["quiz"]["q"]
            p[f"quiz-{poi['id']}-right"] = "Верно! " + poi["quiz"]["explain"]
            p[f"quiz-{poi['id']}-wrong"] = "Не совсем. " + poi["quiz"]["explain"]
        p[f"poi-{poi['id']}"] = text
    p["start"] = "Поехали! Хорошей дороги."
    p["pause"] = "Пауза."
    p["finish"] = "Поездка завершена. Отличная работа!"
    p["gps-lost"] = "Пропал сигнал спутников."
    for i, d in enumerate(DIRECTIONS):
        p[f"off-{i}"] = f"Внимание, вы сошли с маршрута. Трасса на {d}."
    # Сводка каждые пять километров собирается из трёх кусков
    for n in range(5, 100, 5):
        p[f"km-{n}"] = (words(n) + " " + plural(n, "километр", "километра", "километров")).capitalize() + " позади."
    for n in range(3, 46):
        p[f"avg-{n}"] = f"Средняя скорость — {words(n)} " + plural(n, "километр", "километра", "километров") + " в час."
    for n in range(3, 61):
        p[f"last5-{n}"] = f"Последние пять километров — за {words(n, True)} " + plural(n, "минуту", "минуты", "минут") + "."
    return {k: for_ear(v) for k, v in p.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speaker", default="xenia", help="xenia, baya, kseniya — женские; aidar, eugene — мужские")
    ap.add_argument("--model", default="v5_5_ru")
    ap.add_argument("--bitrate", default="48k")
    ap.add_argument("--tempo", type=float, default=1.0)
    ap.add_argument("--cache", default=str(pathlib.Path.home() / ".cache" / "silero-voice"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out = ROOT / "audio" / args.speaker
    out.mkdir(parents=True, exist_ok=True)
    stress = VOICE_DIR / "dicts" / "stress.tsv"
    foreign = VOICE_DIR / "dicts" / "foreign.tsv"
    texts = {k: prepare(v, stress, foreign) for k, v in phrases(load_pois()).items()}

    if args.dry_run:
        dump = "\n\n".join(f"### {k}\n{v}" for k, v in texts.items())
        (out / "texts.txt").write_text(dump, encoding="utf-8")
        print(f"тексты → {out / 'texts.txt'}")
        return

    import tts_silero  # noqa: E402 — torch грузится только для синтеза
    model = tts_silero.load_model(pathlib.Path(args.cache), args.model)
    for old in out.glob("*.mp3"):
        old.unlink()
    digest = hashlib.sha1()
    for i, (key, speech) in enumerate(texts.items(), 1):
        audio = tts_silero.synthesize(model, speech, args.speaker, args.model)
        mp3 = out / f"{key}.mp3"
        tts_silero.save_mp3(audio, mp3, args.bitrate, args.tempo)
        digest.update(mp3.read_bytes())
        print(f"[{i}/{len(texts)}] {mp3.relative_to(ROOT)}")

    manifest = {"dir": f"audio/{args.speaker}/", "speaker": args.speaker,
                "v": digest.hexdigest()[:10], "keys": sorted(texts)}
    (ROOT / "js" / "voice.js").write_text(
        "// Сгенерировано tools/make_audio.py — озвучка голосом Silero.\n"
        "window.VOICE = " + json.dumps(manifest, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"готово: {len(texts)} файлов, js/voice.js обновлён")


if __name__ == "__main__":
    main()
