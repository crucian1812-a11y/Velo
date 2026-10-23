#!/usr/bin/env python3
"""Озвучка русского текста голосом Silero, с подготовкой текста для уха.

    python voice.py текст.txt                         # → текст.mp3 рядом с файлом
    python voice.py глава1.txt глава2.txt --out mp3/  # несколько файлов в одну папку
    python voice.py папка_с_текстами/                 # все .txt из папки
    python voice.py текст.txt --speaker eugene --tempo 0.85
    python voice.py текст.txt --dry-run               # только подготовить текст, без звука

Перед синтезом текст готовится: числа становятся словами в нужном падеже,
латынь переписывается кириллицей с ударением, римские цифры читаются
порядковыми, номера сносок выкидываются, в именах и трудных словах
ставятся ударения из словарей в папке dicts/.

Первый запуск скачивает модель Silero (около сотни мегабайт) в кэш.
"""

import argparse
import pathlib
import shutil
import sys
import time
import wave

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from speech import prepare  # noqa: E402

SPEAKERS = ("xenia", "baya", "kseniya", "aidar", "eugene")


def dicts_dir() -> pathlib.Path | None:
    """Словари лежат в dicts/ рядом с пакетом, а в репозитории книг — в audio/."""
    for cand in (HERE / "dicts", HERE.parent / "audio"):
        if (cand / "stress.tsv").exists():
            return cand
    return None


def read_text(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):        # Блокнот в Windows часто пишет в cp1251
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    sys.exit(f"Не понял кодировку файла {path}: сохраните его в UTF-8")


def collect(inputs: list[str]) -> list[pathlib.Path]:
    files = []
    for item in inputs:
        p = pathlib.Path(item)
        if p.is_dir():
            files += sorted(f for f in p.glob("*.txt") if not f.name.endswith(".speech.txt"))
        elif p.exists():
            files.append(p)
        else:
            sys.exit(f"Нет такого файла или папки: {p}")
    if not files:
        sys.exit("Нечего озвучивать: не нашлось ни одного .txt")
    return files


def save_wav(audio, path: pathlib.Path, rate: int) -> None:
    pcm = (audio.clamp(-1, 1) * 32767).to(__import__("torch").int16).numpy().tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="текстовые файлы или папки с .txt")
    ap.add_argument("--out", help="папка для звука; по умолчанию — рядом с текстом")
    ap.add_argument("--speaker", default="xenia", choices=SPEAKERS,
                    help="голос: xenia, baya, kseniya — женские; aidar, eugene — мужские")
    ap.add_argument("--tempo", type=float, default=1.0,
                    help="темп: 1.0 — как есть, 0.85 — на 15%% медленнее, 1.1 — быстрее")
    ap.add_argument("--model", default="v5_5_ru", help="модель Silero")
    ap.add_argument("--bitrate", default="64k", help="битрейт mp3")
    ap.add_argument("--no-stress", action="store_true", help="не ставить ударения из словарей")
    ap.add_argument("--dry-run", action="store_true",
                    help="только подготовить текст: рядом появится файл .speech.txt")
    ap.add_argument("--cache", default=str(pathlib.Path.home() / ".cache" / "silero-voice"),
                    help="куда класть скачанную модель")
    args = ap.parse_args()

    files = collect(args.inputs)
    found = dicts_dir()
    dicts = None if args.no_stress else found
    foreign = (found or HERE) / "foreign.tsv"   # чтения иностранных слов нужны всегда

    model = None
    for src in files:
        text = read_text(src)
        speech = prepare(text, dicts / "stress.tsv" if dicts else None, foreign)
        out_dir = pathlib.Path(args.out) if args.out else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.dry_run:
            dst = out_dir / f"{src.stem}.speech.txt"
            dst.write_text(speech, encoding="utf-8")
            print(f"{src} → {dst}")
            continue

        if model is None:
            import tts_silero                   # torch грузится только когда нужен звук
            print(f"загружаю модель {args.model}…")
            model = tts_silero.load_model(pathlib.Path(args.cache), args.model)

        started = time.time()
        audio = tts_silero.synthesize(model, speech, args.speaker, args.model)
        seconds = len(audio) / tts_silero.RATE
        if shutil.which("ffmpeg"):
            dst = out_dir / f"{src.stem}.mp3"
            tts_silero.save_mp3(audio, dst, args.bitrate, args.tempo)
            seconds /= args.tempo
        else:
            dst = out_dir / f"{src.stem}.wav"
            save_wav(audio, dst, tts_silero.RATE)
            if args.tempo != 1.0:
                print("ffmpeg не найден: темп не применён, звук сохранён в WAV")
        print(f"{src} → {dst}  ({int(seconds // 60)} мин {int(seconds % 60)} с звука, "
              f"синтез {time.time() - started:.0f} с)")


if __name__ == "__main__":
    main()
