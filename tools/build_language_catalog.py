#!/usr/bin/env python3
"""Build ui/languages.json — every language with all three stages available
(ASR + NLLB + a TTS voice), ordered: the 8 bench-verified first, then the
extended set by descending estimated accuracy.

Validation per language: whisper/SenseVoice code exists, FLORES code exists in
the NLLB vocab, and a TTS voice exists (Piper medium from voices.json, or
Supertonic-3's declared list). WER figures are published-benchmark estimates
for whisper base (FLEURS-derived, rounded); zh/ja/ko use SenseVoice and are
CER-flavored. The 8 verified languages are the ones bench-tested on this device.

Usage: venv/bin/python tools/build_language_catalog.py <piper_voices.json> <out.json>
"""
import json
import sys

from faster_whisper.tokenizer import _LANGUAGE_CODES as WHISPER
import sentencepiece as sp

PIPER_JSON, OUT = sys.argv[1], sys.argv[2]

SUPERTONIC = {"ar", "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr",
              "hi", "hr", "hu", "id", "it", "ja", "ko", "lt", "lv", "nl", "pl",
              "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi"}
SENSEVOICE = {"zh", "ja", "ko"}
VERIFIED = ["zh", "ja", "en", "es", "pt", "de", "ru", "fr"]  # bench-tested, best-first

# code: (native name, flag, FLORES code, est. WER %)
LANGS = {
    "en": ("English", "🇬🇧", "eng_Latn", 6),   "es": ("Español", "🇪🇸", "spa_Latn", 7),
    "fr": ("Français", "🇫🇷", "fra_Latn", 13), "de": ("Deutsch", "🇩🇪", "deu_Latn", 11),
    "pt": ("Português", "🇵🇹", "por_Latn", 9), "ru": ("Русский", "🇷🇺", "rus_Cyrl", 13),
    "zh": ("中文", "🇨🇳", "zho_Hans", 6),       "ja": ("日本語", "🇯🇵", "jpn_Jpan", 6),
    "ko": ("한국어", "🇰🇷", "kor_Hang", 8),     "it": ("Italiano", "🇮🇹", "ita_Latn", 9),
    "ca": ("Català", "🇦🇩", "cat_Latn", 12),   "id": ("Bahasa Indonesia", "🇮🇩", "ind_Latn", 14),
    "pl": ("Polski", "🇵🇱", "pol_Latn", 14),   "nl": ("Nederlands", "🇳🇱", "nld_Latn", 15),
    "uk": ("Українська", "🇺🇦", "ukr_Cyrl", 16), "no": ("Norsk", "🇳🇴", "nob_Latn", 17),
    "tr": ("Türkçe", "🇹🇷", "tur_Latn", 17),   "sv": ("Svenska", "🇸🇪", "swe_Latn", 18),
    "ro": ("Română", "🇷🇴", "ron_Latn", 19),   "bg": ("Български", "🇧🇬", "bul_Cyrl", 20),
    "sk": ("Slovenčina", "🇸🇰", "slk_Latn", 20), "vi": ("Tiếng Việt", "🇻🇳", "vie_Latn", 21),
    "fi": ("Suomi", "🇫🇮", "fin_Latn", 21),    "cs": ("Čeština", "🇨🇿", "ces_Latn", 21),
    "da": ("Dansk", "🇩🇰", "dan_Latn", 22),    "el": ("Ελληνικά", "🇬🇷", "ell_Grek", 23),
    "sl": ("Slovenščina", "🇸🇮", "slv_Latn", 24), "hu": ("Magyar", "🇭🇺", "hun_Latn", 26),
    "sr": ("Српски", "🇷🇸", "srp_Cyrl", 27),   "hr": ("Hrvatski", "🇭🇷", "hrv_Latn", 28),
    "et": ("Eesti", "🇪🇪", "est_Latn", 29),    "he": ("עברית", "🇮🇱", "heb_Hebr", 30),
    "lv": ("Latviešu", "🇱🇻", "lvs_Latn", 30), "lt": ("Lietuvių", "🇱🇹", "lit_Latn", 31),
    "ar": ("العربية", "🇸🇦", "arb_Arab", 32),  "sq": ("Shqip", "🇦🇱", "als_Latn", 32),
    "fa": ("فارسی", "🇮🇷", "pes_Arab", 33),    "is": ("Íslenska", "🇮🇸", "isl_Latn", 34),
    "cy": ("Cymraeg", "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "cym_Latn", 38), "eu": ("Euskara", "🇪🇸", "eus_Latn", 40),
    "ur": ("اردو", "🇵🇰", "urd_Arab", 40),     "hi": ("हिन्दी", "🇮🇳", "hin_Deva", 41),
    "ka": ("ქართული", "🇬🇪", "kat_Geor", 42),  "hy": ("Հայերեն", "🇦🇲", "hye_Armn", 44),
    "sw": ("Kiswahili", "🇹🇿", "swh_Latn", 45), "bn": ("বাংলা", "🇧🇩", "ben_Beng", 46),
    "te": ("తెలుగు", "🇮🇳", "tel_Telu", 47),   "mr": ("मराठी", "🇮🇳", "mar_Deva", 50),
    "ne": ("नेपाली", "🇳🇵", "npi_Deva", 50),   "lb": ("Lëtzebuergesch", "🇱🇺", "ltz_Latn", 52),
    "ml": ("മലയാളം", "🇮🇳", "mal_Mlym", 55),
}

# Piper: cheapest medium voice per language family
piper = {}
for key, v in json.load(open(PIPER_JSON)).items():
    if v.get("quality") != "medium":
        continue
    fam = v["language"]["family"]
    size = sum(f.get("size_bytes", 0) for f in v["files"].values())
    if fam not in piper or size < piper[fam][1]:
        piper[fam] = (key, size)

# NLLB vocab check via the sentencepiece model + known added tokens:
# FLORES codes are added tokens, not sp pieces — validate against the fairseq list.
from transformers.models.nllb.tokenization_nllb import FAIRSEQ_LANGUAGE_CODES
nllb = set(FAIRSEQ_LANGUAGE_CODES)

out = []
problems = []
for code, (name, flag, flores, wer) in LANGS.items():
    if code not in WHISPER and code not in SENSEVOICE:
        problems.append(f"{code}: no ASR"); continue
    if flores not in nllb:
        problems.append(f"{code}: {flores} not in NLLB"); continue
    if code in piper:
        tts, voice, size_mb = "piper", piper[code][0], round(piper[code][1] / 1e6)
    elif code in SUPERTONIC:
        tts, voice, size_mb = "supertonic", "supertonic-3", 0
    else:
        problems.append(f"{code}: no TTS voice"); continue
    out.append({
        "code": code, "name": name, "flag": flag, "flores": flores,
        "wer": wer, "verified": code in VERIFIED,
        "asr": "sensevoice" if code in SENSEVOICE else "whisper",
        "tts": tts, "ttsVoice": voice, "voiceMB": size_mb,
    })

# One flat ordering, purely by accuracy (best first); verified breaks ties.
catalog = sorted(out, key=lambda e: (e["wer"], not e["verified"], e["name"]))
json.dump(catalog, open(OUT, "w"), ensure_ascii=False, indent=1)

nv = sum(1 for e in catalog if e["verified"])
print(f"{len(catalog)} languages with all three stages ({nv} bench-verified)")
print(f"piper-voiced: {sum(1 for e in catalog if e['tts']=='piper')}, "
      f"supertonic-only: {[e['code'] for e in catalog if e['tts']=='supertonic']}")
print(f"total voice disk if all downloaded: "
      f"{sum(e['voiceMB'] for e in catalog)} MB")
if problems:
    print("excluded:", problems)
