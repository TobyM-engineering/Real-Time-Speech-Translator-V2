# Translator V2 — Build Log

The narrative record of this build: what we built, what broke, what we
wrongly believed and why, and how each thing was actually resolved.
`CLAUDE.md` holds the *current state* — decisions, constraints, standing
rules. This file holds the *story*, because the wrong theories are where the
lessons live.

**Convention (standing, 2026-08-27):** every stage sign-off appends a dated
section here — built / broke / wrongly believed / resolved. Newest at the
bottom.

---

## Where this started

Version 1 was a Pi Zero 2 W with one mic, streaming a room to OpenAI's
realtime API and playing English into one ear. It worked, cost about $65
plus ~$10 per half hour of API time, and taught us most of what's hard about
this problem: feedback between speaker and mic, Bluetooth that dies under
load, buffers that silently hoard latency, and the fact that a decision made
after audio has played is worthless. Version 2 is the same idea grown up:
two people, two mics, two ears, a touchscreen, every model local on a Pi 5,
and no network at all. The conversation never leaves the device.

---

## 2026-08-26 — day one on the bench

### Setup, and two small mysteries

SSH kept dropping on idle: fixed server-side with keepalives (60 s interval,
3 strikes). The screen kept blanking despite every setting saying it
shouldn't. **Wrong theory:** something was re-enabling blanking behind our
backs. **Truth:** a stale pre-upgrade `labwc` autostart file carried a
`swayidle … wlopm --off` line; Toby's `apt full-upgrade` replaced the file
and the problem evaporated. Lesson recorded: on this OS, blanking lives in
exactly three autostart files, check them first.

### Test A — do the two mics really arrive as two channels?

The DJI Mic Mini receiver enumerates as a class-compliant USB audio device:
stereo, 24-bit, 48 kHz, one transmitter per channel. Speech into TX1 landed
on the left channel +11.5 dB over the right; TX2 mirrored it. The quiet
channel's energy is the *other* live mic hearing the room acoustically —
which is precisely what ratio-based speaker arbitration wants. The mapping
survived a full power cycle (+10.7 dB, same node name, stereo mode
persisted). TX identity fixed by windscreen: black = TX1 = left = person A.
Closed on day one, no fallback hardware needed.

### The stack goes in

All models installed and smoke-tested the same day: Silero VAD, SenseVoice,
whisper base int8, NLLB-200-600M int8 (converted on-device), Piper voices,
Supertonic-3. One landmine surfaced only later in a disk audit: **wrong
mental model — "pip uninstall torch removes torch."** The temporary torch
install (needed once, for the NLLB conversion) had pulled 2.9 GB of nvidia
CUDA libraries plus 0.65 GB of triton, because aarch64 torch wheels target
NVIDIA ARM servers. Purging the family took the venv from 4.4 GB to 851 MB.

### First honest latency numbers

Live over the mic, 18 turns, English→Spanish: median **5.34 s** from
stop-speaking to Spanish audio (min 3.41, max 7.63). **Wrong assumption in
the plan:** ASR would dominate. **Measured truth:** translation is ~52 % of
a turn (2.78 s of 5.34); whisper costs a near-flat ~1.5–1.8 s regardless of
utterance length; the 0.5 s endpoint silence is an information-theoretic
floor. Every latency decision since traces back to this table.

### Test B — AirPods as two independent ears

**Wrong assumption:** we'd need SBC-XQ for channel isolation. AirPods Pro
don't offer it — they renegotiate plain SBC even when forced. Ear-tested
plain SBC joint stereo instead: simultaneous hard-panned speech clean, and —
the case that actually matters — one ear at speech level with the other
near-silent, clean in both directions. The XQ requirement was retired on
evidence. One trap recorded for posterity: `bluez5.roles` names the *Pi's*
role; `a2dp_sink` is the receive-only direction and makes earbud connects
fail with `br-connection-profile-unavailable`. And the AirPods mic is
unreachable by construction (`roles = [ a2dp_source ]`) — the HFP profile
doesn't exist to switch to.

### Why NLLB invents words, diagnosed

The model hallucinated on short fragments ("5" → "5 El"; "9, 10, 11, 12." →
an appended "¿Qué es eso?"). **Wrong theory #1: punctuation or ellipsis
triggers it.** Ruled out — bare "or." hallucinates too. **Wrong data:** the
first VAD sweep decimated a 16 kHz capture as if it were 48 kHz; every
number from it was thrown away and remeasured. **Truth:** the trigger is
*content starvation*, not incompleteness — six words of real content anchor
the encoder ("I went to the store and" translates faithfully, dangling
conjunction preserved), while lone digits let the decoder's training prior
leak out verbatim (beam 4 turned "5" into "Los Estados miembros"). Separate
discovery: greedy decoding silently dropped the second sentence of a
two-sentence input — fixed for free by splitting per sentence, which beam 2
would have fixed at 2× the cost. Out of this came the whole fragment policy:
0.7 s endpoint, merge window, digits passthrough, output detector.

### The screen

Field research (Timekettle, Pocketalk, Google's face-to-face mode, hospital
kiosks, two-player board games) converged on the split-and-rotated layout,
and three directions were mocked up; Toby chose **Signal** — full-field
state colors, one huge word, plus a single latest-line transcript that
doubles as a tap-to-cancel band. **Wrong number caught in review:** the
first type-size pass said ≥37 px was readable; re-derived at the true panel
geometry (62.3 × 110.7 mm active, 293.7 ppi, 0.5 m viewing) the comfort
line is **≥48 px**. Toolkit decided by measurement, not fashion: PySide6/QML
ran fullscreen at 105 MB alongside the fully loaded models.

A side mystery got two wrong answers before the right one: sudoers appeared
to have changed mid-session. **Wrong theory #1:** Toby had hardened it.
**Wrong theory #2:** a `userconf-pi` package change. **Truth:** this install
*never had* passwordless sudo — RPi OS stopped shipping it in late 2025; the
directory mtime was dpkg conffile bookkeeping during the day's upgrade, and
the early session's password-free sudo rode a shared auth ticket from Toby's
own interactive command. Both wrong attributions are retracted in CLAUDE.md.

### Eight languages become fifty-one

Computing the honest intersection (whisper × NLLB × available voices) gave
51 languages with all three stages. Two corrections along the way: the
"Piper has no Japanese voice" claim was outdated (ja and ko voices exist
now), and **machine-translating single UI words is a register minefield** —
Korean "speaking" came back as 이야기 (*story*), French "muted" as *Le
silence*. The six status words are now hand-curated for all 51 languages;
only full sentences go through NLLB.

### The pipeline, designed before built

Seven threads, bounded queues, a turn registry, and a gate ledger — written
as a design doc first, with nine flagged decisions. The ones that shaped
everything: **D5, never drop audio** — bound backlog *socially* by asking
the speaker to pause, in their own language, because the speaker is the only
person who can't feel the lag; **D7, strictly one turn at a time**; and the
bystander correction — the energy ratio distinguishes the two wearers from
each other only; someone leaning into your chest mic *is* dominant on your
channel, and per-person mute is the only defense. Stated as a limitation
instead of pretended away.

### Stage 1 — capture, arbitration, gate (log-only)

Three fights: (1) writing WAV to a FIFO fails — libsndfile can't seek a
pipe; capture became raw pw-record stdout. (2) An easy arbitration case
measured a suspiciously thin +7.3 dB — pauses inside the segment diluted the
ratio toward zero; recomputed over speech-active frames only. (3) **Wrong
assumption: sherpa's `max_speech_duration` caps segments.** It doesn't,
reliably — it counts continuous *voiced* runs and resets at breath pauses
(17–26 s segments sailed past a 10 s cap). The pipeline now enforces its own
wall-clock cap and treats sherpa's as decoration.

### Stage 2 — ASR meets the live screen

The densest bug day. Python 3.13 clobbered a method named `_handle` with the
Thread's internal handle (`'_ThreadHandle' object is not callable`) — avoid
`_handle` and `_stop` on Thread subclasses. A "stuck" pipeline turned out to
be **grep block-buffering eating the logs** — py-spy stack-dumped the
process and proved it healthy while only the logging was broken. Pending
counters leaked on empty/merged/cancelled turns until every turn got exactly
one terminal event.

The live LISTENING indicator took four rounds: raw VAD lit the wrong half on
bleed → gated by a running energy ratio → still flashed at onset (the window
had no speech frames yet) → onset hold plus a since-onset window. A residual
flash in fan noise with a mic an arm away *facing* the speaker was accepted
and documented rather than chased — the real chest-mic geometry is better
than the bench's.

Two proposals died here, correctly. **Mine:** group-cancel that held
playback until speech ended — Toby rejected it, and my supporting math was
wrong: I claimed ~3 s of post-speech work; on saturated cores compute is
serial at ≥1× real time, so a 30 s paragraph finishes 15–20 s after speech
ends, not 3. Display-only grouping was built instead — one growing band,
playback untouched. **His, preemptively:** pinning each half's ASR to its
selected language as a bleed filter — recorded as considered-and-rejected
because whisper transcribes wrong-language audio as *confident nonsense*
rather than rejecting it. The seam-repair merge hold survived only because
Toby demanded the log evidence before removal — and the log showed turn #10
genuinely merging turn #11 at gap −0.00 s.

### Stage 3 — sound reaches the ears

The self-mixed stereo output was spiked first, as ordered, and the spike
earned its keep twice: `pw-play` reading stdin needs `--raw` (libsndfile
sniffs pipes and dies), and the default 64 KB OS pipe was hiding **1.3
seconds** of buffering — shrunk via `F_SETPIPE_SZ` to ~100 ms. The first
full live loop ended with the anti-feedback gate discarding B's mic picking
up the device's own Spanish — the design working against real audio on its
first outing.

Then the humbling one. Toby played two minutes of video into a mic: the
pipeline fell **147 seconds behind live with zero warnings**. The D5
pause-request thresholds existed only in the design doc — `backlog_seconds()`
was written and never called. A resolved decision, silently unimplemented.
It got wired for real, then the metric itself was found wrong: "seconds of
untranscribed audio" oscillates 10↔0 at chunk cadence while the listener
falls minutes behind, because translation and the ear queue — the stages
that actually grow without bound — weren't counted. **Amended (Toby):** the
metric is end-to-end lag, utterance-end to audio-reaching-the-sink, with
thresholds re-derived (meter 8 s, SOFT 15, HARD 30). One narrow exception to
never-drop was granted and documented: during *Bluetooth recovery only*,
queued translated audio older than 10 s is discarded — stale speech a minute
late is worse than a gap.

### The bad evening — one "regression" that was three faults and a hardware ghost

Transcription went to garbage, and the diagnosis chased four wrong theories
in sequence. **Theory 1: the D5 amendment broke it** ("this is a regression,
revert"). The log exonerated the amendment completely. **What it actually
was, part 1:** three *independent* faults stacked — a stale 2 GB
model-holding process I'd left running for six hours (memory pressure →
decode blowups), accidental panel state from stray touches during hardware
tests (A muted and switched to Español), and a genuinely low worn-mic level.
**Theory 2: chest-mount geometry** — Test A had verified levels mouth-held,
never worn, and worn read ~−40 dBFS. Partially right, but the fix (**+8 dB
digital capture gain**, after the DJI app's own gain maxed out without
reaching the verified band) didn't fix transcription. **Theory 3: room
noise** — silent doctor runs suggested speech stood only ~5 dB over the
floor. **Truth, finally:** a *transient DJI transmitter hardware state*.
Toby power-cycled both transmitters and worn speech recovered on its own to
−19.3 dBFS, 21 dB over the room — at which point the +8 dB gain was pushing
a healthy signal into clipping territory and was removed entirely, returning
every threshold to its bench-verified operating point. Standing rule born
that night: **if worn levels collapse, power-cycle the transmitters first**
— before app gain, before software. And: verify levels in the wearing
position, not held at the mouth.

### Stage 4 — the supervisor, and the destructive test that fired nothing

v1's recovery logic was ported: capture reopen with died-instantly vs
died-after-running discrimination, a Bluetooth watchdog with an escalating
ladder (reconnect → adapter power-cycle → root-approved
`systemctl restart bluetooth`, armed by a one-command sudoers rule), thermal
hysteresis. Then Toby closed the AirPods case mid-session and the case-close
test **fired no rung at all**: the disconnect fell between 5-second polls
(trusted buds auto-reconnect fast) and writes never stalled, so all the
queued audio played late in order and the stale-drop never ran. Fixed with
an event-driven `bluetoothctl` disconnect monitor — any disconnect, however
brief, now triggers a stream rebuild and stale-drop on reconnect. The
backlog meter's blank spells got values logged (`D5m`) so a metric can never
again go unrecorded.

---

## 2026-08-26 (late) → 08-27 — the interaction-and-speed arc

### The doctor breaks at the worst moment, then gets better

`tools/doctor.py` crashed with `No module named 'src'` — the import sat
mid-file *after* the six-second speak test, and running by path doesn't put
the project root on Python's search list. Fixed, import moved to the top so
failures are instant, and the mic step grew a background-noise readout and a
speech-over-room check, because digital gain can never improve that ratio.
Its process check also learned to ignore wrapper shells after a false "more
than one translator running" (the nohup launcher's command line merely
*quoted* the pipeline's name).

### A mic check that lives on the glass

The centre-strip ready dot became a tap target: a level panel with a live
bar per channel, the verified −26…−21 dBFS band marked, speech and room
levels with the gap between them, and a plain verdict pointing at the DJI
app since the Pi can't move the RX gain. Doctor's measurement code moved to
`src/levels.py` so the panel and the CLI run *the same* definition of good.

### Hold-to-talk

Press-and-hold your half for one second → your channel is accepted without
arbitration and the other channel is dropped outright. The gate is bypassed
too — safe because the hold itself breaks any feedback loop (the return path
is the dropped channel). Mute still beats everything. A segment straddling
press or release counts as held, which matters because the closing segment
of every hold arrives ~0.7 s *after* release.

Bench round 1 found it half-working: "speech only came out after release."
**Toby's theory:** segments only close on endpoint silence. **Right for
holds under 10 s, wrong for his 22 s hold** — the 10 s cap *did* fire
mid-hold and MT *did* start mid-hold; the real killer was chunk economics
(10 s of speech → ASR 2.6 + MT 13.2 + TTS 2.6 ≈ 19 s to first audio, worse
under contention — the same audio decoded in 8.5 s against a running MT vs
2.3 s idle). Two latent bugs fell out of the same log read: the ASR
merge-hold deadline is computed from the *pre-decode* clock, so nearly every
hold expires ~0.02 s after it starts; and a zero-length flush artifact was
being accepted as a real turn.

### The gate tells on itself

The forensic interval logging (added when a live gate event printed a
suspicious 100 %) caught the truth: **"84 % overlap" printed where the real
geometry was ~22 %.** Two code causes, read not guessed: `gate.py` sums
margin-extended ledger intervals *without merging* (neighbouring extensions
double-count), and `playback.py` wall-stamps 20 ms frames that complete in
bursts through the 85 ms pipe, fragmenting continuous audio into islands
that undercount coverage ~4×. The numbers: true 22 · printed 84 · islands
alone 6 · islands-with-margins-merged 25. Diagnosed, recorded, **not yet
fixed** — it needs its own bench round.

### "French is twice as slow" — a confound, not a fact

Measured properly: French MT is a real but modest 1.2–1.6× Spanish, driven
entirely by *output token count* (greedy decode time tracks output length;
the one French sentence that came out shorter ran faster). The 15-vs-7
impression came from comparing short Spanish sentences against 10-second
five-sentence French paragraphs, plus a one-time 2.5 s voice load (French
wasn't preloaded), plus queue backlog. Like-for-like 10 s chunks: Spanish
18.8 s, French 17.0–17.3 s. French was never slower.

### The optimization menu — measured, then picked one at a time

Profiling produced a ranked menu with three headline measurements:
SenseVoice does English **17–30× faster** than whisper (RTF ~0.05 flat vs a
fixed ~1.5–2.6 s cost); NLLB at 4 threads is only 8–11 % faster and risks
the reserved core; and capture's overflow drop was **silent** —
`except queue.Full: pass`, no log, ever. Toby picked in order:

1. **Voice preload** — both selected voices load before READY; a language
   change preloads on the TTS thread; LRU eviction learned to never evict a
   currently selected voice (or the stall returns through the back door).
   The overflow drop now logs instantly on first drop, then running totals.
2. **Mid-speech chunk closing (dip-cut)** — at 4 s of continuous speech, cut
   at a real energy gap rather than an arbitrary sample. **Wrong first
   threshold:** 0.30 × median energy — physically impossible, the room floor
   sits at 0.40 × median; measured on real captures and retuned to 0.55 for
   ≥0.12 s (stop-consonant closures are shorter — that's what keeps cuts out
   of the middle of words). Dip-cut chunks are deliberately *not* seams, or
   the merge window would quietly rebuild the 10 s serial turns. Verified
   offline by streaming real dump WAVs through the real frontend: cuts land
   between words, one growing band, translation starts while you talk.
3. **English → SenseVoice** — same clips, 1.54→0.08 s and 2.33→0.53 s;
   full-path compute per second of speech dropped from 1.10–1.88× to
   0.78–1.48×, under 1.0 on sustained speech. Known cost: sparse punctuation
   on long input. Lived for about eight hours before being superseded.

### Parakeet

Toby asked for a benchmark against whisper on "the es/fr/de/pt clips in the
dump folder." **Premise correction: no such clips existed** — every dump was
his English. Substituted Piper-synthesized clips with exact ground truth
plus the model repo's native-speaker WAVs, bias stated. Two traps dodged on
the way: `/tmp` is RAM-backed tmpfs on this box (models go to the SD card),
and the official sherpa-onnx conversion of Parakeet TDT 0.6B v3 exists — no
torch needed.

Results were decisive: **RTF 0.115–0.132 on four ARM cores** (3–4.7× faster
than whisper base), **WER 5.3 % vs 15.4 %** on ground-truth clips — whisper
hallucinated whole phrases ("Je voudrais acheter deux billets" → "Je vous
présente 2 billets") where Parakeet was perfect on 6 of 8 — full punctuation
and casing, ~1.3 GB RAM, 641 MB disk. All four models resident measured
2.5 GB; the full stack projects to ~3.3 GB of 8. Recommended FOR, wired the
next request: Parakeet takes its 24 catalog languages **including English**
(the partition put en in the European set, which also fixes SenseVoice's
punctuation gap), SenseVoice keeps zh/ja/ko, whisper keeps the tail.

Cost measured honestly with the page cache force-evicted: **cold boot READY
29.7 s** (was ~12–14 s), warm restart 13.5 s. One more trap for the record:
regenerating `ui/languages.json` from the catalog builder alone **strips all
51 languages' localized UI strings** — the enriched file is the product of a
second step. Routing was merged into the enriched file instead; exactly 24
`asr` fields changed, verified.

---

## Open at time of writing

- Gate overlap arithmetic: both bugs diagnosed with numbers, fix designed
  (sample-accumulated ledger stamps + merged margin sums), not yet built.
- ASR merge-hold deadline bug (pre-decode clock): fix will *restore* holds
  the pipeline currently isn't paying — pair with halved hold values.
- Run-on fallback MT split for long unpunctuated transcripts.
- Real-mic verification of Parakeet on Spanish/French — the adoption gate.
- The deferred hardware/system tail: kiosk boot (cage), read-only rootfs,
  EEPROM settings, WiFi-off mode, D9 ja voice A/B, Test C when the UPS
  arrives.
- Phase 2 proper: clause-streamed MT→TTS — the largest remaining latency
  win on the menu.

---

## 2026-08-27 — day close-out

One open question answered: the first hold-to-talk try that captured nothing
was Toby **holding silently to watch the frame appear** — nothing was
spoken, so nothing was missed. Closed without a code change; the follow-up
holds in the same session captured and streamed correctly.

State at shutdown: the pipeline runs three-engine ASR routing (Parakeet for
24 European languages including English, SenseVoice for zh/ja/ko, whisper
for the tail), 4-second dip-cut chunking so translation starts mid-speech,
hold-to-talk, the centre-dot mic check, voice preload with a gated READY,
capture gain back at 0, and the armed supervisor. Cold boot to READY is
29.7 s, warm restart 13.5 s. Nothing autostarts yet — next boot, launch
manually with `venv/bin/python -m src.stage3_main`.

Preserved out of the RAM-backed scratchpad before shutdown (it does not
survive power-off): the five measurement scripts CLAUDE.md cites, the eight
ground-truth bench clips with their reference texts, and
`piper_voices.json` — the catalog generator's required input — now all under
`tools/`. Everything is committed; the working tree is clean.

---

## 2026-08-28 — the repo leaves the SD card; the hardware record meets the bench

**Built:** the project's first remote. Toby created a private GitHub repo
(`TobyM-engineering/Real-Time-Speech-Translator-V2`); before anything was
pushed, the **entire git history** — every blob in every commit, not just
the working tree — was scanned for secrets: API keys, provider token
shapes (`ghp_`, `github_pat_`, `hf_`, `sk-`, `AKIA`), private-key blocks,
WiFi credentials, `.env`-class filenames. Verdict: clean. Every
"password"/"token" hit was prose ("passwordless sudo", "per-token
logprob"); the only WAVs in history are the eight deliberate ground-truth
bench clips. Identifiers that *do* ship (authorship email, AirPods MAC,
DJI serial, hostname) were disclosed before pushing — fine for a private
repo, worth remembering if it ever goes public. `.gitignore` gained
belt-and-braces rows (logs, dump WAVs, stray model files, `.env`) on top
of the existing `models/`+`venv/`; the tracked payload is 2.4 MB across
57 files. `master` was renamed `main`, GitHub's README commit merged with
`--allow-unrelated-histories`, and the push landed with upstream tracking.
**Pushing is now part of the sign-off habit** — the SD card is no longer
the only copy of two days of hard-won fixes.

**Broke, twice, on the way to authenticated:** the first "credentials are
stored" turned out to be a credentials file containing the literal string
`PASTE_TOKEN_HERE` — the placeholder had been copied verbatim. Then the
real fine-grained token authenticated as the right account but got HTTP
404 for its own repo: the token's **Repository access** step had never
included it (the classic left-on-"Public repositories" default). Editing
the token's grants — value unchanged, nothing to re-store — fixed it.
The token itself ended up pasted in chat despite the keep-it-out-of-the-
transcript suggestion; it is tightly scoped (this repo, Contents only)
and flagged for rotation at Toby's convenience.

**Wrongly believed, in the hardware record:** two claims the parts audit
killed. The table said an official ML2032 RTC battery was connected to
the BAT header — the connector is in fact **empty** (an XYGStudy
RTC-Bat-B is on hand, not yet fitted; it matters because the offline
device has no NTP). And the wiring notes still described the stock
200 mm display ribbon — the bench actually runs a **longer official FPC
ribbon** (length unmeasured) so the screen reaches in the stacked layout.
Both corrected inline with supersession notes, same convention as every
stale claim before them.

**Resolved into the record as confirmed-in-use, with links:** the
Waveshare UPS HAT (E) with its I2C gauge at 0x2d already integrated
(battery pips, level-panel detail, low-battery fault), four 21700 cells
(brand/model honestly unverified), the NFHK 90° down-angled USB adapter
carrying the DJI receiver — recorded as a **deliberate part choice whose
down-angle orientation is load-bearing** (other angles foul the stack),
and the DJI receiver as its own line item (ALSA card 2, name `Rx`).
Raspberry Pi OS moved out of the parts table: it is software, not a part.

## 2026-08-28 — first real two-person bench: three faults, three commits

The first genuine two-person session (English on A, Spanish-attempting partner on B, ~27 min) surfaced three faults; each was investigated log-first, then fixed as its own verified commit.

**What broke, and what was wrongly believed first.** (1) B's Spanish came out as English phonetics ("bien" → "B N.", "¿cuánto cuestan?" → "Conto question."). Suspected wrong-engine routing — the log disproved that (B ran es(parakeet) throughout, engine and config agreed everywhere): the real fault is parakeet's INTERNAL language ID, which sherpa exposes no way to pin (verified: transducer config is three filenames), flipping on short/accented non-native speech. The native-speaker bench clips had hidden this — the accent confound. (2) Suspected pipeline truncation of English tails — every candidate resolved innocent (dip-cut continuations, a speaker yielding to interruption, one parakeet under-decode of 4.0 s to "Yes."); the tail rescue correctly fired zero times. (3) Interruptions dropped both speakers with the one-at-a-time hint — measured: 45 overlap episodes, 38 benign turn-taking bleed, 7 genuine both-speaking (~39 s), ~17 s actually lost. Bonus find with its own history: GATE_TRIM survivors had NEVER reached ASR — arbitration promised "remainder would proceed", the frontend dispatch was binary, a day-one stage-3 bug that ate three of B's barge-in replies at ratios up to +17.7 dB.

**The fixes.** Commit 1 (af3acd7) completed the trim path: longest playback-free run from the ledger geometry, ratio re-test on the kept audio, ≥0.5 s survivors become turns; harness proved a real clip's kept half decoding where the whole sentence used to die; the three live events' lost text is unrecoverable — the dump tap keeps the FIRST 8 turns, not the "recycling" set the record claimed (corrected). Commit 2 (0c39548) reversed the 08-27 routing partition: parakeet is en-only; whisper takes the other 23 European languages WITH an explicit language argument (the empty-decode rescue got pinned too, after rescuing Spanish as Italian). Measured price: +1.4–1.9 s per non-English turn, and parakeet remains more accurate on clean native speech — traded for never leaving the configured language. Commit 3 queues ambiguous-band speech captured during simultaneous talk and decodes it after the dominant speaker finishes (0.8 s sustained quiet — the harness caught a dip-cut flush releasing mid-monologue before it shipped; depth cap 2, stale 10 s). Expected recovery: ~12 of the 17 lost seconds, plus ~4.5 s from the trim fix.

## 2026-08-28 (late) — the silent-death class closed: crash fix, liveness watchdog, honest D5

The evening two-person session ended with the UI stuck on a hard "please pause" for five minutes. The investigation found the ASR worker thread dead — killed by an IndexError in the stage-2 dangling-word check ('.' from whisper-fr on a 0.4 s blip passed a guard that tested one string and indexed another) — while 21 turns queued into the corpse, the capture/UI/MT/TTS threads all ran on happily, and the AirPods disconnect that followed was initially mistaken for the story. Three commits, each verified:

1. **The crash** (344d206): logic extracted to `_last_content_word()`, guard and index the same list; `tests/` directory born with a regression suite (punctuation-only transcripts return '' forever).
2. **The watchdog** (5cd55f5): the supervisor now polls every worker thread; a dead one raises an on-glass fault naming the stage, is rebuilt in place with its queue migrated, and a second death within 300 s escalates to a whole-pipeline execv restart. Harness-verified live: sabotaged decode → detected <5 s → rebuilt warm in 7.2 s → real decode; second kill → escalation fired.
3. **D5 honesty**: pause-requests are suppressed while a fault pill explains the backlog (no more "please pause" beside "earbuds not connected"), and an unexplained HARD that never drains for 120 s declares the pipeline wedged and restarts it — with the earbuds-in-case case deliberately excluded so it cannot exec-loop.

Wrongly believed along the way: that the backlog warning itself was the fault (it was arithmetically perfect — one clock, exact ages); and, briefly, that the earbuds disconnect caused the stall (it happened 115 s after the crash).
