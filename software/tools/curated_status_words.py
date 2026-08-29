#!/usr/bin/env python3
"""Hand-curated status words for all 51 catalog languages, written to software
localization conventions (the way a phone's own interface labels these states)
— NOT machine-translated. NLLB output remains for the full-sentence strings.

Order per language: ready, listening, translating, speaking, muted, cancelled.
UNSURE marks languages where these are best-effort conventions that should get
a native-speaker glance before shipping; they are stored with
status_confidence = "curated_unverified".

Merges into ui/languages.json. Usage: venv/bin/python tools/curated_status_words.py
"""
import os
import json

KEYS = ["ready", "listening", "translating", "speaking", "muted", "cancelled"]

STATUS = {
    "en": ["Ready", "Listening", "Translating", "Speaking", "Muted", "Cancelled"],
    "es": ["Listo", "Escuchando", "Traduciendo", "Hablando", "Silenciado", "Cancelado"],
    "fr": ["Prêt", "Écoute", "Traduction", "Lecture", "Muet", "Annulé"],
    "de": ["Bereit", "Zuhören", "Übersetzen", "Wiedergabe", "Stumm", "Abgebrochen"],
    "pt": ["Pronto", "Ouvindo", "Traduzindo", "Falando", "Silenciado", "Cancelado"],
    "ru": ["Готово", "Слушаю", "Перевожу", "Говорю", "Микрофон выкл.", "Отменено"],
    "zh": ["就绪", "聆听中", "翻译中", "朗读中", "已静音", "已取消"],
    "ja": ["準備完了", "聞き取り中", "翻訳中", "読み上げ中", "ミュート", "キャンセル"],
    "ko": ["준비됨", "듣는 중", "번역 중", "말하는 중", "음소거", "취소됨"],
    "it": ["Pronto", "In ascolto", "Traduzione", "Riproduzione", "Silenziato", "Annullato"],
    "ca": ["A punt", "Escoltant", "Traduint", "Parlant", "Silenciat", "Cancel·lat"],
    "id": ["Siap", "Mendengarkan", "Menerjemahkan", "Berbicara", "Dibisukan", "Dibatalkan"],
    "pl": ["Gotowe", "Słuchanie", "Tłumaczenie", "Odtwarzanie", "Wyciszone", "Anulowane"],
    "nl": ["Gereed", "Luisteren", "Vertalen", "Spreken", "Gedempt", "Geannuleerd"],
    "uk": ["Готово", "Слухаю", "Перекладаю", "Говорю", "Без звуку", "Скасовано"],
    "no": ["Klar", "Lytter", "Oversetter", "Snakker", "Dempet", "Avbrutt"],
    "tr": ["Hazır", "Dinliyor", "Çevriliyor", "Konuşuyor", "Sessize alındı", "İptal edildi"],
    "sv": ["Redo", "Lyssnar", "Översätter", "Talar", "Tystad", "Avbruten"],
    "ro": ["Gata", "Ascultare", "Traducere", "Redare", "Fără sunet", "Anulat"],
    "bg": ["Готово", "Слуша", "Превежда", "Говори", "Заглушен", "Отказано"],
    "sk": ["Pripravené", "Počúva", "Prekladá", "Hovorí", "Stlmené", "Zrušené"],
    "vi": ["Sẵn sàng", "Đang nghe", "Đang dịch", "Đang nói", "Đã tắt tiếng", "Đã hủy"],
    "fi": ["Valmis", "Kuuntelee", "Kääntää", "Puhuu", "Mykistetty", "Peruutettu"],
    "cs": ["Připraveno", "Poslouchá", "Překládá", "Mluví", "Ztlumeno", "Zrušeno"],
    "da": ["Klar", "Lytter", "Oversætter", "Taler", "Lydløs", "Annulleret"],
    "el": ["Έτοιμο", "Ακρόαση", "Μετάφραση", "Αναπαραγωγή", "Σίγαση", "Ακυρώθηκε"],
    "sl": ["Pripravljeno", "Posluša", "Prevaja", "Govori", "Utišano", "Preklicano"],
    "hu": ["Kész", "Hallgatás", "Fordítás", "Lejátszás", "Némítva", "Megszakítva"],
    "sr": ["Спремно", "Слуша", "Преводи", "Говори", "Без звука", "Отказано"],
    "hr": ["Spremno", "Sluša", "Prevodi", "Govori", "Utišano", "Otkazano"],
    "et": ["Valmis", "Kuulab", "Tõlgib", "Räägib", "Vaigistatud", "Tühistatud"],
    "he": ["מוכן", "מאזין", "מתרגם", "מדבר", "מושתק", "בוטל"],
    "lv": ["Gatavs", "Klausās", "Tulko", "Runā", "Apklusināts", "Atcelts"],
    "lt": ["Paruošta", "Klausosi", "Verčia", "Kalba", "Nutildyta", "Atšaukta"],
    "ar": ["جاهز", "يستمع", "يترجم", "يتحدث", "مكتوم", "تم الإلغاء"],
    "sq": ["Gati", "Po dëgjon", "Po përkthen", "Po flet", "Në heshtje", "Anuluar"],
    "fa": ["آماده", "در حال گوش دادن", "در حال ترجمه", "در حال صحبت", "بی‌صدا", "لغو شد"],
    "is": ["Tilbúið", "Hlustar", "Þýðir", "Talar", "Þaggað", "Hætt við"],
    "cy": ["Barod", "Yn gwrando", "Yn cyfieithu", "Yn siarad", "Wedi distewi", "Wedi canslo"],
    "eu": ["Prest", "Entzuten", "Itzultzen", "Hizketan", "Isilduta", "Ezeztatuta"],
    "ur": ["تیار", "سن رہا ہے", "ترجمہ ہو رہا ہے", "بول رہا ہے", "خاموش", "منسوخ"],
    "hi": ["तैयार", "सुन रहा है", "अनुवाद हो रहा है", "बोल रहा है", "म्यूट", "रद्द"],
    "ka": ["მზადაა", "უსმენს", "თარგმნის", "საუბრობს", "დადუმებულია", "გაუქმებულია"],
    "hy": ["Պատրաստ է", "Լսում է", "Թարգմանում է", "Խոսում է", "Անձայն", "Չեղարկված"],
    "sw": ["Tayari", "Inasikiliza", "Inatafsiri", "Inazungumza", "Imenyamazishwa", "Imeghairiwa"],
    "bn": ["প্রস্তুত", "শুনছে", "অনুবাদ করছে", "বলছে", "মিউট করা", "বাতিল"],
    "te": ["సిద్ధం", "వింటోంది", "అనువదిస్తోంది", "మాట్లాడుతోంది", "మ్యూట్", "రద్దు చేయబడింది"],
    "mr": ["तयार", "ऐकत आहे", "भाषांतर करत आहे", "बोलत आहे", "म्यूट", "रद्द केले"],
    "ne": ["तयार", "सुन्दै छ", "अनुवाद गर्दै छ", "बोल्दै छ", "म्यूट", "रद्द गरियो"],
    "lb": ["Prett", "Lauschtert", "Iwwersetzt", "Schwätzt", "Stomm", "Annulléiert"],
    "ml": ["തയ്യാർ", "കേൾക്കുന്നു", "വിവർത്തനം ചെയ്യുന്നു", "സംസാരിക്കുന്നു", "മ്യൂട്ട്", "റദ്ദാക്കി"],
}

# Best-effort conventions — get a native-speaker glance before shipping these.
UNSURE = {"sq", "is", "cy", "eu", "ka", "hy", "te", "mr", "ne", "lb", "ml", "lv"}

CAT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/software/ui/languages.json"
catalog = json.load(open(CAT))
missing = [e["code"] for e in catalog if e["code"] not in STATUS]
assert not missing, f"catalog languages without curated words: {missing}"

for e in catalog:
    words = STATUS[e["code"]]
    for k, w in zip(KEYS, words):
        e["ui"][k] = w
    # NLLB flags applied only to the surviving machine-translated sentences
    e["ui_flags"] = [f for f in e.get("ui_flags", []) if f not in KEYS]
    e["status_confidence"] = ("curated_unverified" if e["code"] in UNSURE
                              else "curated")

json.dump(catalog, open(CAT, "w"), ensure_ascii=False, indent=1)
n_unsure = sum(1 for e in catalog if e["status_confidence"] == "curated_unverified")
print(f"merged curated status words for {len(catalog)} languages; "
      f"{n_unsure} marked curated_unverified: {sorted(UNSURE)}")
rem = [(e['code'], e['ui_flags']) for e in catalog if e['ui_flags']]
print("remaining NLLB sentence flags:", rem or "none")
