#!/usr/bin/env python3
"""Build src/interjections_data.json from en.wiktionary.org translation
tables — every entry cited to its source page, none generated from memory.

Per concept: an English headword, gloss keywords to pick the right sense's
translation table, and optional per-language PICKS (choosing among the
CITED candidates when the first listed is the wrong register for a
standalone utterance — the pick must exist in the fetched list or the cell
goes uncovered). Rows adapted beyond the cited form are marked
judgement=true with a note. Skipped words and every uncovered
language-word cell are recorded in the output's meta block.

Usage: venv/bin/python tools/build_interjections.py <cache_dir> <out.json>
"""
import json
import re
import subprocess
import sys
import time
import unicodedata

CACHE, OUT = sys.argv[1], sys.argv[2]

# words whose standalone meaning flips with context — excluded by rule
SKIPPED = {
    "here":  "flips: handing something over vs stating location",
    "there": "flips: location vs comforting interjection",
    "stop":  "citable translations are infinitives — wrong register for a "
             "shouted command; per-language imperatives need hand curation",
    "excuse_me": "only citable sense is the farewell; recognition variants "
                 "merged into 'sorry' (judgement call)",
    "again": "no translation table for the 'once more' sense — the "
             "near-miss tables would miscite",
    "good_night": "no direct table — wiktionary only cross-references "
                  "('see you tomorrow'); candidate for a signed-off "
                  "hand-curated exception",
    "youre_welcome": "reply-sense filed under 'of course'/'no problem'; "
                     "recognition folded into no_problem (function-"
                     "equivalent reply, judgement call)",
    "youre_right": "only a cross-reference to verb 'be right' — wrong "
                   "register",
    "how":   "interrogative sense has no translation table on wiktionary "
             "(only the 'How!' greeting is tabled) — wrong-sense data "
             "forbidden by the citation rule",
}

# concept -> (headword page, gloss keywords for sense selection, question?)
WORDS = {
    # existing concepts, now re-cited from source
    "yes":       ("yes", ["affirm", "agreement"], False),
    "no":        ("no", ["negat", "denial", "refus"], False),
    "okay":      ("OK", ["approv", "acknowledg", "agree"], False),
    "thanks":    ("thank you", ["gratitude", "thanks"], False),
    "hello":     ("hello", ["greeting"], False),
    "bye":       ("goodbye", ["farewell", "parting"], False),
    "sorry":     (None, [], False),   # no citable interjection table —
                                      # legacy 10-language cells only
    "please":    ("please", ["polite", "request"], False),
    "wait":      ("wait", ["delay", "remain", "hold"], False),
    # the expansion
    "what":      ("what", ["which thing", "interrogative"], True),
    "why":       ("why", ["for what reason", "for what purpose"], True),
    "where":     ("where", ["what place", "location", "interrogative"], True),
    "when":      ("when", ["what time", "interrogative"], True),
    "who":       ("who", ["what person", "which person"], True),
    "really":    ("really", ["surprise", "doubt", "indicating"], True),
    "maybe":     ("maybe", ["possibly", "perhaps"], False),
    "sure":      ("sure", ["noncommittal"], False),
    "help":      ("help", ["cry", "distress", "emergency"], False),
    "slowly":    ("slowly", ["slow pace"], False),
    "no_problem": ("no problem", ["reassur", "no matter", "response"], False),
    "good":      ("good", ["positive", "pleasing", "acceptable"], False),
    "bad":       ("bad", ["unfavorable", "negative", "unpleasant"], False),
    "of_course": ("of course", ["certainly", "obviously", "acknowledg"], False),
    "exactly":   ("exactly", ["agreement", "precisely"], False),
    "nothing":   ("nothing", ["not any thing", "no thing"], False),
    "never":     ("never", ["at no time"], False),
    "always":    ("always", ["at all times", "every time"], False),
    "now":       ("now", ["at the present", "immediately"], False),
    "later":     ("later", ["some time in the future"], False),
    "more":      ("more", ["greater", "additional"], False),
    "enough":    ("enough", ["stop", "sufficient"], False),
    # 2026-08-27 expansion 2: phrasebook-class utterances (selection evidence:
    # Wiktionary Category:English phrasebook membership + Toby's named set)
    "good_morning":   ("good morning", ["morning"], False),
    "good_afternoon": ("good afternoon", ["afternoon"], False),
    "good_evening":   ("good evening", ["evening"], False),
    "welcome":        ("welcome", ["greeting", "arrival"], False),
    "see_you_later":  ("see you later", ["farewell", "goodbye"], False),
    "take_care":      ("take care", ["farewell", "parting", "goodbye"], False),
    "good_luck":      ("good luck", ["luck", "wish"], False),
    "congratulations": ("congratulations", ["congratulat", "praise"], False),
    "happy_birthday": ("happy birthday", ["birthday"], False),
    "me_too":         ("me too", ["likewise", "same", "also"], False),
    "why_not":        ("why not", ["assent", "agree", "reason"], True),
    "how_much":       ("how much", ["price", "cost", "what quantity"], True),
    "how_many":       ("how many", ["what number", "quantity"], True),
    "lets_go":        ("let's go", ["hortative", "suggestion", "let us",
                       "movement"], False),
    "dont_worry":     ("don't worry", ["reassur", "worry"], False),
    "no_thanks":      ("no, thanks", ["polite", "refus", "declin"], False),
    "got_it":         ("got it", ["understand", "understood",
                       "acknowledg"], False),
    "i_know":         ("I know", ["aware", "agree", "known"], False),
    "thats_all":      ("that's all", ["nothing more", "conclud"], False),
    "it_depends":     ("it depends", ["depend", "uncertain"], False),
    "im_fine":        ("I'm fine", ["well", "fine", "response"], False),
}

# wiktionary language label -> our catalog code (nested labels handled too)
LANGS = {
    "Spanish": "es", "French": "fr", "German": "de", "Italian": "it",
    "Portuguese": "pt", "Mandarin": "zh", "Japanese": "ja", "Korean": "ko",
    "Russian": "ru", "Dutch": "nl", "Polish": "pl", "Ukrainian": "uk",
    "Turkish": "tr", "Vietnamese": "vi", "Indonesian": "id", "Hindi": "hi",
    "Arabic": "ar", "English": "en",
    # free harvest: same citations, zero marginal cost, catalog languages
    "Bengali": "bn", "Urdu": "ur", "Persian": "fa", "Thai": "th",
    "Swedish": "sv", "Greek": "el", "Czech": "cs", "Romanian": "ro",
    "Hungarian": "hu", "Danish": "da", "Finnish": "fi", "Slovak": "sk",
    "Bulgarian": "bg", "Croatian": "hr", "Catalan": "ca", "Hebrew": "he",
    "Slovene": "sl", "Lithuanian": "lt", "Latvian": "lv", "Estonian": "et",
    "Bokmål": "no", "Serbo-Croatian": None,  # ambiguous script — skip
}
REQUIRED = ["es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ru", "nl",
            "pl", "uk", "tr", "vi", "id", "hi", "ar"]

# register/conversational PICKS among cited candidates (form must be in the
# fetched candidate list, else the cell is uncovered). Selection, not memory.
PICKS = {
    ("slowly", "es"): "despacio",
    ("slowly", "pt"): "devagar",
    ("nothing", "ja"): "何でもない",
    ("where", "ja"): "どこ",
    ("when", "ja"): "いつ",
    ("why", "ja"): "どうして",
    ("who", "ja"): "だれ",
    ("welcome", "zh"): "欢迎",
    ("see_you_later", "zh"): "回见",
    ("see_you_later", "de"): "bis später",
    ("enough", "de"): "das genügt",
}

# cells whose only cited candidates are the wrong register for a standalone
# utterance (e.g. bare infinitives) — excluded, logged uncovered
DROPS = {("help", "ja"), ("more", "ja"), ("sure", "ja"), ("sure", "zh"),
         ("why", "es"), ("really", "zh"), ("lets_go", "ja"),
         ("why_not", "zh"), ("thats_all", "zh")}  # wrong-register/wrong-word candidates

# script normalization: cited traditional-Chinese forms rendered in the
# catalog's simplified script (mechanical char mapping, not translation)
TRAD2SIMP = str.maketrans("沒麼甚謝對見長訴幫問請誰為時裡裏遲總現當許確壞從題會歡臨運樂緊況聽", "没么什谢对见长诉帮问请谁为时里里迟总现当许确坏从题会欢临运乐紧况听")


# The pre-expansion table (src/interjections.py, 2026-08-27): bench-exposed
# renderings keep priority for their 10 languages; where the same form
# appears among the fetched candidates it gains the citation, otherwise the
# row stays marked judgement with source "curated (pre-expansion)".
LEGACY_RENDER = {
    "yes":    {"en": "Yes.", "es": "Sí.", "fr": "Oui.", "de": "Ja.",
               "pt": "Sim.", "it": "Sì.", "ru": "Да.", "zh": "是的。",
               "ja": "はい。", "ko": "네."},
    "no":     {"en": "No.", "es": "No.", "fr": "Non.", "de": "Nein.",
               "pt": "Não.", "it": "No.", "ru": "Нет.", "zh": "不是。",
               "ja": "いいえ。", "ko": "아니요."},
    "okay":   {"en": "Okay.", "es": "Vale.", "fr": "D'accord.",
               "de": "Okay.", "pt": "Está bem.", "it": "Va bene.",
               "ru": "Хорошо.", "zh": "好的。", "ja": "わかりました。",
               "ko": "알겠어요."},
    "thanks": {"en": "Thank you.", "es": "Gracias.", "fr": "Merci.",
               "de": "Danke.", "pt": "Obrigado.", "it": "Grazie.",
               "ru": "Спасибо.", "zh": "谢谢。", "ja": "ありがとうございます。",
               "ko": "감사합니다."},
    "hello":  {"en": "Hello.", "es": "Hola.", "fr": "Bonjour.",
               "de": "Hallo.", "pt": "Olá.", "it": "Ciao.",
               "ru": "Здравствуйте.", "zh": "你好。", "ja": "こんにちは。",
               "ko": "안녕하세요."},
    "bye":    {"en": "Goodbye.", "es": "Adiós.", "fr": "Au revoir.",
               "de": "Tschüss.", "pt": "Tchau.", "it": "Arrivederci.",
               "ru": "До свидания.", "zh": "再见。", "ja": "さようなら。",
               "ko": "안녕히 가세요."},
    "sorry":  {"en": "Sorry.", "es": "Perdón.", "fr": "Pardon.",
               "de": "Entschuldigung.", "pt": "Desculpe.", "it": "Scusa.",
               "ru": "Извините.", "zh": "对不起。", "ja": "すみません。",
               "ko": "죄송합니다."},
    "please": {"en": "Please.", "es": "Por favor.", "fr": "S'il vous plaît.",
               "de": "Bitte.", "pt": "Por favor.", "it": "Per favore.",
               "ru": "Пожалуйста.", "zh": "麻烦你了。", "ja": "お願いします。",
               "ko": "부탁합니다."},
    "wait":   {"en": "One moment.", "es": "Un momento.", "fr": "Un instant.",
               "de": "Einen Moment.", "pt": "Um momento.", "it": "Un momento.",
               "ru": "Минутку.", "zh": "等一下。", "ja": "ちょっと待ってください。",
               "ko": "잠시만요."},
}
LEGACY_RECOGNIZE = {
    "en": {"yes": "yes", "yeah": "yes", "yep": "yes", "yup": "yes",
           "uh huh": "yes", "no": "no", "nope": "no", "nah": "no",
           "okay": "okay", "ok": "okay", "alright": "okay",
           "all right": "okay", "thanks": "thanks", "thank you": "thanks",
           "hello": "hello", "hi": "hello", "hey": "hello",
           "bye": "bye", "goodbye": "bye", "see you": "bye",
           "sorry": "sorry", "my bad": "sorry", "excuse me": "sorry",
           "pardon me": "sorry", "pardon": "sorry", "i m sorry": "sorry",
           "please": "please", "morning": "good_morning",
           "afternoon": "good_afternoon", "evening": "good_evening",
           "good day": "good_morning",
           "my pleasure": "no_problem",
           "you re welcome": "no_problem", "many thanks": "thanks",
           "thanks a lot": "thanks", "thank you very much": "thanks",
           "so long": "bye", "later": "see_you_later",
           "congrats": "congratulations", "no thanks": "no_thanks",
           "that s it": "thats_all", "no worries": "no_problem",
           "wait": "wait", "wait a moment": "wait", "wait a second": "wait",
           "hold on": "wait", "one moment": "wait", "one second": "wait"},
    "es": {"sí": "yes", "si": "yes", "no": "no", "vale": "okay",
           "ok": "okay", "okay": "okay", "de acuerdo": "okay",
           "gracias": "thanks", "hola": "hello", "adiós": "bye",
           "adios": "bye", "chao": "bye", "perdón": "sorry",
           "perdon": "sorry", "lo siento": "sorry", "por favor": "please"},
    "fr": {"oui": "yes", "ouais": "yes", "non": "no", "d accord": "okay",
           "ok": "okay", "okay": "okay", "merci": "thanks",
           "bonjour": "hello", "salut": "hello", "au revoir": "bye",
           "pardon": "sorry", "désolé": "sorry", "desole": "sorry",
           "s il vous plaît": "please", "s il te plaît": "please"},
    "de": {"ja": "yes", "jawohl": "yes", "nein": "no", "nee": "no",
           "okay": "okay", "ok": "okay", "in ordnung": "okay",
           "danke": "thanks", "danke schön": "thanks", "hallo": "hello",
           "tschüss": "bye", "auf wiedersehen": "bye",
           "entschuldigung": "sorry", "bitte": "please"},
    "pt": {"sim": "yes", "não": "no", "nao": "no", "ok": "okay",
           "okay": "okay", "tá bom": "okay", "ta bom": "okay",
           "obrigado": "thanks", "obrigada": "thanks", "olá": "hello",
           "ola": "hello", "oi": "hello", "tchau": "bye", "adeus": "bye",
           "desculpa": "sorry", "desculpe": "sorry", "por favor": "please"},
    "it": {"sì": "yes", "si": "yes", "no": "no", "va bene": "okay",
           "ok": "okay", "okay": "okay", "grazie": "thanks",
           "ciao": "hello", "arrivederci": "bye", "scusa": "sorry",
           "scusi": "sorry", "per favore": "please"},
    "ru": {"да": "yes", "нет": "no", "хорошо": "okay", "ладно": "okay",
           "окей": "okay", "спасибо": "thanks", "привет": "hello",
           "здравствуйте": "hello", "пока": "bye", "до свидания": "bye",
           "извините": "sorry", "извини": "sorry", "простите": "sorry",
           "пожалуйста": "please"},
    "zh": {"是": "yes", "是的": "yes", "对": "yes", "嗯": "yes",
           "不": "no", "不是": "no", "不行": "no", "好": "okay",
           "好的": "okay", "行": "okay", "谢谢": "thanks", "你好": "hello",
           "再见": "bye", "对不起": "sorry", "抱歉": "sorry"},
    "ja": {"はい": "yes", "うん": "yes", "ええ": "yes", "いいえ": "no",
           "いや": "no", "オーケー": "okay", "わかった": "okay",
           "わかりました": "okay", "了解": "okay", "ありがとう": "thanks",
           "ありがとうございます": "thanks", "こんにちは": "hello",
           "さようなら": "bye", "じゃあね": "bye", "ごめんなさい": "sorry",
           "すみません": "sorry", "お願いします": "please"},
    "ko": {"네": "yes", "예": "yes", "응": "yes", "아니요": "no",
           "아니": "no", "알겠어요": "okay", "오케이": "okay",
           "좋아요": "okay", "감사합니다": "thanks", "고마워요": "thanks",
           "안녕하세요": "hello", "안녕히 가세요": "bye", "잘 가": "bye",
           "죄송합니다": "sorry", "미안해요": "sorry", "부탁합니다": "please"},
}

_T = re.compile(r"\{\{tt?\+?\|([a-zA-Z-]+)\|([^|}]+)")  # t/t+/tt/tt+ (multitrans)
_STRESS = re.compile("[́̀]")


def clean(form, code=None):
    form = form.replace("[[", "").replace("]]", "")
    form = re.sub(r"\([^)]*\)", "", form)          # editorial parentheses
    if code in ("ru", "uk"):
        # editorial stress marks — Cyrillic only; Latin acutes are spelling
        form = unicodedata.normalize("NFC", _STRESS.sub("",
                  unicodedata.normalize("NFD", form)))
    if code == "zh":
        if "/" in form:                      # script-variant lists: keep last
            form = [x for x in form.split("/") if x][-1]
        form = form.translate(TRAD2SIMP)
    return re.sub(r"\s+", " ", form).strip()


def fetch(page):
    key = re.sub(r"[^A-Za-z0-9]+", "_", page)
    path = f"{CACHE}/wk_{key}.txt"
    try:
        cached = open(path, encoding="utf-8").read()
        if cached and not cached.lstrip().startswith("<!DOCTYPE"):
            return cached
    except OSError:
        pass
    url = ("https://en.wiktionary.org/w/index.php?title="
           + page.replace(" ", "%20") + "&action=raw")
    for attempt in range(5):
        # -f: a 404 (page does not exist) exits 22 with empty output —
        # definitive, cache the miss; only throttle pages deserve retries
        r = subprocess.run(["curl", "-sf", "--max-time", "30",
                            "-A", "TranslatorV2-interjection-builder/1.0 "
                                  "(offline translator bench device)",
                            url], capture_output=True, text=True)
        body = r.stdout
        time.sleep(0.7)
        if r.returncode == 22 or (r.returncode == 0
                                  and not body.lstrip().startswith("<!DOCTYPE")):
            open(path, "w", encoding="utf-8").write(body)
            return body
        time.sleep(2.0 * (attempt + 1))
    return ""


def coverage(body):
    return sum(1 for c in parse_langs(body) if c in REQUIRED)


def pick_block(text, keywords):
    """Among all trans-top blocks (main page + /translations subpage
    merged), prefer keyword-matching glosses; within those, take the one
    covering the most required languages — big entries scatter senses
    across many small tables. No keyword match -> best-covered block,
    marked as a gloss fallback."""
    blocks = re.findall(r"\{\{trans-top\|([^}]*)\}\}(.*?)\{\{trans-bottom\}\}",
                        text, re.S)
    if not blocks:
        return None, None, False
    matched = [(g, b) for g, b in blocks
               if any(k in g.lower() for k in keywords)]
    best_all = max(blocks, key=lambda gb: coverage(gb[1]))
    if matched:
        best_m = max(matched, key=lambda gb: coverage(gb[1]))
        # a keyword hit on a midget side-sense loses to the main table
        if coverage(best_m[1]) >= min(3, coverage(best_all[1])):
            return best_m[0], best_m[1], False
    return best_all[0], best_all[1], True


def parse_langs(body):
    out = {}
    for line in body.splitlines():
        m = re.match(r"^\*+:?\s*([A-Za-zÅåö-]+(?: [A-Za-z]+)?):", line.strip())
        if not m:
            continue
        label = m.group(1)
        code = LANGS.get(label)
        if not code:
            continue
        cands = [clean(f, code) for _, f in _T.findall(line)]
        cands = [c for c in cands if c and "{{" not in c and len(c) < 40]
        if cands:
            out.setdefault(code, [])
            for c in cands:
                if c not in out[code]:
                    out[code].append(c)
    return out


def punctuate(form, code, question):
    if form.rstrip()[-1:] in "?!.。？！…؟":
        return form.rstrip()                    # already punctuated as cited
    if code in ("zh", "ja"):
        return form + ("？" if question else "。")
    if code == "ar":
        return form + ("؟" if question else ".")
    if code == "es" and question:
        return "¿" + form + "?"
    return form + ("?" if question else ".")


concepts, meta_uncovered, meta_fallback = {}, [], []
for concept, (page, keywords, question) in WORDS.items():
    if page is None:
        entry = {}
        gloss, fell_back = "(legacy only)", False
        # jump straight to the legacy overlay below
        text = ""
    else:
        text = fetch(page)
    if page is not None:
        # large entries keep their tables on a /translations subpage
        sub = fetch(page + "/translations")
        if "trans-top" in sub:
            text = text + "\n" + sub
        gloss, body, fell_back = pick_block(text, keywords)
    else:
        body = ""
    if page is not None and body is None:
        for code in REQUIRED:
            meta_uncovered.append(f"{concept}/{code} (no translation table "
                                  f"on '{page}')")
        print(f"!! {concept}: no trans tables on '{page}'", flush=True)
        continue
    if fell_back:
        meta_fallback.append(f"{concept}: gloss keywords missed; used first "
                             f"table ('{gloss.strip()[:60]}')")
    by_code = parse_langs(body) if body else {}
    entry = {}
    for code, cands in by_code.items():
        if (concept, code) in DROPS:
            meta_uncovered.append(f"{concept}/{code} (cited candidates are "
                                  f"wrong register for standalone use)")
            continue
        want = PICKS.get((concept, code))
        if want is not None:
            if want in cands:
                form, judgement, note = want, True, "register pick among cited candidates"
            else:
                meta_uncovered.append(f"{concept}/{code} (pick '{want}' not "
                                      f"in cited candidates {cands[:4]})")
                continue
        else:
            form, judgement, note = cands[0], False, ""
        if " " in form and code in ("zh", "ja", "th"):
            form = form.replace(" ", "")
        entry[code] = {
            "text": punctuate(form, code, question),
            "recognize": cands,
            "source": f"en.wiktionary.org/wiki/{page.replace(' ', '_')} "
                      f"(gloss: {gloss.strip()[:50]})",
            "judgement": judgement or fell_back,
            **({"note": note} if note else {}),
        }
    # English renders as the headword itself — the tables never list the
    # source language, and the headword IS the citation
    if page is not None and "en" not in entry:
        head = page[0].upper() + page[1:]
        entry["en"] = {"text": punctuate(head, "en", question),
                       "recognize": [page], "source": "headword",
                       "judgement": False}
    # legacy overlay: bench-exposed renderings keep priority; they gain the
    # wiktionary citation when the same form is among the cited candidates
    for code, legacy_text in LEGACY_RENDER.get(concept, {}).items():
        bare = legacy_text.strip("¿").rstrip(".。？?!").strip()
        prior = entry.get(code)
        cands = prior["recognize"] if prior else []
        cited = any(bare.casefold() == c.casefold() for c in cands)
        entry[code] = {
            "text": legacy_text,
            "recognize": sorted({*cands, bare}),
            "source": (prior["source"] if cited
                       else "curated (pre-expansion table, 2026-08-27)"),
            "judgement": not cited,
            "note": ("legacy form, confirmed by citation" if cited
                     else "legacy form, not among cited candidates"),
        }
    for code in REQUIRED:
        if code not in entry:
            meta_uncovered.append(f"{concept}/{code} (no cited candidate)")
    concepts[concept] = {"question": question, "langs": entry}
    got = sum(1 for c in REQUIRED if c in entry)
    print(f"{concept:11s} '{page}' -> {len(entry)} languages "
          f"({got}/{len(REQUIRED)} required){' [gloss fallback]' if fell_back else ''}",
          flush=True)

out = {
    "generated": "tools/build_interjections.py, 2026-08-27, en.wiktionary.org",
    "skipped_by_rule": SKIPPED,
    "gloss_fallbacks": meta_fallback,
    "uncovered": sorted(meta_uncovered),
    "extra_recognize": LEGACY_RECOGNIZE,
    "concepts": concepts,
}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n_cells = sum(len(c["langs"]) for c in concepts.values())
print(f"\n{len(concepts)} concepts, {n_cells} cited cells, "
      f"{len(meta_uncovered)} uncovered required cells, "
      f"{len(meta_fallback)} gloss fallbacks", flush=True)
print(f"skipped by context-flip rule: {list(SKIPPED)}", flush=True)
