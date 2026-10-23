#!/usr/bin/env python3
"""Озвучка голосами Silero — русская модель, понимает ударения.

    python3 scripts/tts_silero.py text.txt --out audio/out --speakers aidar,eugene

Ударение ставится знаком «+» перед ударной гласной: «Герак+лит». Где знака
нет, модель расставляет ударения сама. Цифры модель не читает — их надо
разворачивать словами заранее.

Лицензия моделей CC BY-NC 4.0: для личного прослушивания годится.
Модель качается с models.silero.ai, поэтому запускать в раннере.
"""

import argparse
import os
import pathlib
import re
import subprocess
import wave

import torch
import torch.package

# v5 различает омографы («з+амок» и «зам+ок»), с v5_4 — ещё и интонацию вопроса.
MODEL = "v5_5_ru"
MODEL_URL = "https://models.silero.ai/models/tts/ru/{}.pt"
RATE = 48000
# Больше за раз модель не берёт: на длинном куске она падает.
CHUNK = 800


def load_model(cache: pathlib.Path, model_id: str):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{model_id}.pt"
    if not path.exists():
        torch.hub.download_url_to_file(MODEL_URL.format(model_id), str(path))
    model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    return model


def chunks(paragraph: str) -> list[str]:
    """Нарезать абзац по предложениям в куски не длиннее CHUNK."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?…])\s+", paragraph):
        if cur and len(cur) + len(sent) + 1 > CHUNK:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def silence(seconds: float) -> torch.Tensor:
    return torch.zeros(int(RATE * seconds))


def say(model, piece: str, speaker: str, flags: dict) -> torch.Tensor | None:
    """Прочитать кусок; если модель падает — упростить текст и попробовать ещё.

    На целой книге всегда найдётся сочетание знаков, на котором модель
    спотыкается. Лучше потерять ударение или кавычку в одном куске, чем
    уронить прогон на середине книги."""
    tries = [piece,
             re.sub(r"[^А-Яа-яЁё+\s.,!?:;—-]", " ", piece),
             re.sub(r"[^А-Яа-яЁё\s.,!?—-]", " ", piece)]
    for k, text in enumerate(tries):
        text = re.sub(r"\s+", " ", text).strip()
        if not re.search(r"[А-Яа-яЁё]", text):
            return None
        try:
            return model.apply_tts(text=text, speaker=speaker, sample_rate=RATE, **flags)
        except Exception as e:                      # noqa: BLE001 — модель бросает что угодно
            print(f"::warning::кусок не прочитался ({type(e).__name__}: {e}), попытка {k + 2}: "
                  f"{piece[:80]!r}")
    return None


def synthesize(model, text: str, speaker: str, model_id: str) -> torch.Tensor:
    flags = {"put_accent": True, "put_yo": True}
    if model_id.startswith("v5"):
        flags |= {"put_stress_homo": True, "put_yo_homo": True}
    parts = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for pi, para in enumerate(paragraphs):
        for piece in chunks(para):
            audio = say(model, piece, speaker, flags)
            if audio is None:
                continue
            parts += [audio, silence(0.25)]
        if pi + 1 < len(paragraphs):
            parts.append(silence(0.45))
    return torch.cat(parts)


def tempo_filter(tempo: float) -> list[str]:
    """Растянуть речь по времени, не меняя высоту голоса.

    rubberband для речи звучит чище, чем atempo; если ffmpeg собран без
    него, берём atempo — он есть всегда."""
    if abs(tempo - 1.0) < 1e-3:
        return []
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    if " rubberband " in filters:
        return ["-af", f"rubberband=tempo={tempo}"]
    return ["-af", f"atempo={tempo}"]


def save_mp3(audio: torch.Tensor, mp3: pathlib.Path, bitrate: str,
             tempo: float = 1.0) -> None:
    wav = mp3.with_suffix(".wav")
    pcm = (audio.clamp(-1, 1) * 32767).to(torch.int16).numpy().tobytes()
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    *tempo_filter(tempo),
                    "-codec:a", "libmp3lame", "-b:a", bitrate, "-ac", "1", str(mp3)],
                   check=True)
    wav.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--out", default="audio/out")
    ap.add_argument("--speakers", default="aidar,eugene",
                    help="мужские: aidar, eugene; женские: baya, kseniya, xenia")
    ap.add_argument("--name", default="", help="основа имени файла; по умолчанию — имя текста")
    ap.add_argument("--model", default=MODEL, help="v5_5_ru, v4_ru и т. п.")
    ap.add_argument("--cache", default=".voices/silero")
    ap.add_argument("--bitrate", default="64k")
    ap.add_argument("--tempo", type=float, default=1.0,
                    help="меньше единицы — медленнее: 0.85 значит на 15%% медленнее")
    args = ap.parse_args()

    torch.set_num_threads(os.cpu_count() or 1)
    text = pathlib.Path(args.text).read_text(encoding="utf-8")
    if re.search(r"\d", text):
        print("ВНИМАНИЕ: в тексте цифры — Silero их пропустит или исказит")
    model = load_model(pathlib.Path(args.cache), args.model)
    tag = re.sub(r"[^0-9]", "", args.model.split("_ru")[0]) or args.model

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = args.name or pathlib.Path(args.text).stem
    for speaker in [s.strip() for s in args.speakers.split(",") if s.strip()]:
        suffix = "" if abs(args.tempo - 1.0) < 1e-3 else f"-{round(args.tempo * 100)}"
        mp3 = out / f"{stem}--silero-v{tag}-{speaker}{suffix}.mp3"
        save_mp3(synthesize(model, text, speaker, args.model), mp3, args.bitrate, args.tempo)
        print(f"{mp3} — {mp3.stat().st_size // 1024} КиБ")


if __name__ == "__main__":
    main()
