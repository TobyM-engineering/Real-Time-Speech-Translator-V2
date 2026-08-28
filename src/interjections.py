"""Curated interjection table (2026-08-27): one-to-three-word conversational
utterances bypass NLLB entirely — measured, EVERY one-word input draws the
model's subtitle-dialog prior ("Yeah." -> "- Sí, es cierto.", "Okay." ->
"- ¿Qué quieres?" which PASSED the old detector and would have been spoken).
Deterministic table in, deterministic speech out.

Coverage: the 10 languages we can curate confidently (phrasebook register,
matching the hand-curated status words' conventions). An uncovered target
falls back to normal MT — logged, never silent. ru/zh/ja/ko renderings are
curated_unverified pending a native glance, same flag as the status words.
"""
import re

_STRIP = re.compile(r"[\s.,!?¡¿。、！？…\"'\-–—]+")


def _norm(text):
    return _STRIP.sub(" ", text.casefold()).strip()


# concept -> target-language rendering (what gets SPOKEN)
CONCEPTS = {
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
    # added after measuring "Wait a moment." -> "¿Qué pasa?" (fluent
    # hallucination that passes every detector signal — table or nothing)
    "wait":   {"en": "One moment.", "es": "Un momento.", "fr": "Un instant.",
               "de": "Einen Moment.", "pt": "Um momento.", "it": "Un momento.",
               "ru": "Минутку.", "zh": "等一下。", "ja": "ちょっと待ってください。",
               "ko": "잠시만요."},
}

# source-language variants -> concept (keys are _norm()-normalized)
RECOGNIZE = {
    "en": {"yes": "yes", "yeah": "yes", "yep": "yes", "yup": "yes",
           "uh huh": "yes", "no": "no", "nope": "no", "nah": "no",
           "okay": "okay", "ok": "okay", "alright": "okay",
           "all right": "okay", "thanks": "thanks", "thank you": "thanks",
           "hello": "hello", "hi": "hello", "hey": "hello",
           "bye": "bye", "goodbye": "bye", "see you": "bye",
           "sorry": "sorry", "my bad": "sorry", "please": "please",
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


def match(sentence, src_code):
    """Concept key if this sentence is a known interjection, else None."""
    table = RECOGNIZE.get(src_code)
    if not table:
        return None
    return table.get(_norm(sentence))


def render(concept, tgt_code):
    """Curated target-language form, or None if this target is uncovered."""
    return CONCEPTS.get(concept, {}).get(tgt_code)
