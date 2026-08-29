# How It Works

The design decisions behind the device, and the measurements that forced them. Most of these were arrived at the hard way — the narrative version, including the wrong theories, is in [build_log.md](build_log.md).

---

## The basic pipeline

One USB receiver delivers both microphones as a synchronised stereo pair. Everything downstream depends on that: two separate USB devices would drift apart, and the whole speaker-identification scheme would collapse.

```
capture (stereo, 16 kHz)
   → per-channel voice activity detection
   → segment closing (endpoint silence, or a mid-speech "dip cut")
   → speaker arbitration + anti-feedback gate    ← decisions happen HERE
   → speech recognition   (one of three engines, chosen by language)
   → translation          (opus-mt if available, else NLLB)
   → speech synthesis
   → hard-panned stereo mixer → earbuds
```

Seven threads: UI, capture/VAD/arbitration, recognition, translation, synthesis, playback, and a supervisor. Queues between them are bounded except the recognition queue, which is deliberately unbounded — the design rule is *never drop captured speech*; bound the backlog socially instead by asking the speaker to slow down.

**Decisions are made before compute is spent.** Arbitration and the anti-feedback gate both run at segment-accept, not after recognition. A decision made after audio has already played is useless, and a decision made after a 2-second decode has wasted a core.

---

## Who is speaking

**Never use absolute loudness.** Use the cross-channel energy ratio per utterance.

A chest microphone hears its wearer 10–17 dB louder than the other person's microphone does, regardless of how loudly either talks. Measured on this build: wearers land at +10 to +18 dB, bleed at −15 to −17 dB. So:

- ratio ≥ **+6 dB** → this person's own voice → accept
- ratio ≤ **−6 dB** → the other person's voice bleeding in → drop
- in between → ambiguous (simultaneous speech, or a voice from across the table)

Two refinements were forced by real sessions:

**Tail rescue.** The last words of an utterance decay toward the noise floor, where the ratio compresses — a tail measured +3.8 dB on a sentence whose body measured +15, and was being discarded. A short segment starting immediately after the same channel's accepted turn is now rescued from the ambiguous band, unless the other person's indicator is lit.

**Overlap deferral, and its regression.** Ambiguous speech captured while the other channel was also live used to be discarded outright — both people interrupted, both lost. It is now queued and decoded once the dominant speaker stops. But the first version deferred the *whole* ambiguous band, and the negative half is precisely where the *other* person's voice dominates: a live session decoded one person's Spanish on the other's channel, translated it, and played it back into her own ear thirty seconds later. Deferral is now restricted to ratios ≥ 0 dB.

**The limitation this cannot solve:** a bystander leaning toward someone's chest microphone is genuinely dominant on that channel and passes every test. No signal available on this hardware separates "the wearer" from "someone talking into the wearer's microphone." Per-person mute is the only defence.

---

## Not translating our own output

Each person's earbud sits inches from their own chest microphone. Synthesised speech leaks back in at roughly speaking volume and would be re-translated in an endless loop.

The fix is cheap because the system knows exactly when it is playing: the playback mixer writes every interval into a ledger, and segment-accept discards or trims any audio overlapping playback into that person's ear. Two bugs in that mechanism are worth knowing about, because both were invisible:

- **Interval arithmetic.** The ledger's intervals are extended by a margin and then *merged* before summing. Summing them unmerged double-counts on a fragmented ledger — it once reported 84% overlap where the true geometry was 22%.
- **Trimming was never finished.** The code computed how much of a segment would survive a partial overlap, logged it, and then discarded the whole segment anyway. It read as working for months. Partial overlaps now really are trimmed to the longest playback-free run and re-tested.

---

## Timing: one clock, and only one

This caused more wasted debugging than any other single issue, so it deserves its own section.

There were three clocks in the system: wall-clock time, the capture stream's sample counter, and the VAD library's *internal* sample counter. They diverge — the VAD's counter drifted 38 seconds from the capture head over a 9-hour run, and the capture stream itself runs +5 to +66 seconds ahead of wall clock under I/O load, for reasons still not understood.

Mixing them produced failures that looked like everything except a clock problem: the anti-feedback gate silently comparing timestamps from different number lines and never matching; a backlog meter reporting the pipeline was 40 seconds behind when audio was arriving a second later.

**Everything timing-critical now uses the capture sample clock**, converted at a single point. Wall clock is used only for telemetry, and a monitor logs the divergence so capture anomalies are visible rather than silent.

---

## Latency, and why the floor is where it is

Measured, end of speech to audio in the other ear: **~2.5 s** for a short English turn on a fast translation pair, **~3–4 s** with whisper recognition, **3.5–5 s per sentence** when falling back to NLLB.

The remaining floor is roughly 2.0–2.2 s, and it is **mostly deliberate**:

| Component | Cost | Movable? |
|---|---|---|
| Endpoint silence | 0.7 s | No — you cannot know a sentence ended until it has |
| Recognition | ~0.2–2 s | Engine-dependent |
| Cancel window | ≥1.0 s | Only by giving up cancellation |
| Translation | 0.3 s (opus) / 2–3 s (NLLB) | Model choice |
| Synthesis + output | ~0.4 s | Mostly fixed |

Three things bought most of the improvement:

**Mid-speech chunking.** Instead of waiting for the speaker to stop, a segment closes after ~4 seconds at a real energy dip — a gap between words, found by watching for block energy below 55% of the run's median for at least 0.12 s. Translation starts while you are still talking. The threshold is measured, not guessed: the room floor sits at ~40% of speech median, so anything below that could never trigger.

**Per-pair translation models.** NLLB-200 translates any language to any other, which is remarkable and slow (~2–3 s/sentence). Small per-pair opus-mt models do one direction each at ~0.3 s — a 7× improvement measured on identical sentences, with quality at parity. They only exist for some pairs, so NLLB stays resident as the universal fallback, and every fallback is logged rather than silent.

**Turning off a retry ladder nobody asked for.** The recognition library retries at five temperatures with sampling when its own quality check fails, which accented audio triggers constantly — a measured 2.8× multiplier, up to 26 decoder passes for one short sentence. Disabling it produced *better* text on every clip tested and removed the pathological tail.

Overlap discipline matters too: recognition and translation together run slightly slower than real time, so sustained talking backlogs without bound. That is why the screen shows each person's state, and why a genuine backlog asks the speaker to pause.

---

## When things go wrong

The design principle: **a failure the user cannot see is worse than a failure they can.**

- **Recovery ladders, not retry loops.** V1 once logged 43 identical failed Bluetooth attempts over eleven minutes. Recovery here escalates: reconnect → power-cycle the adapter → restart the Bluetooth service, with connection state polled independently of write failures.
- **Worker liveness.** A recognition thread once died on an unhandled exception while everything else kept running: the interface showed "translating" forever and twenty turns queued into a dead consumer. Every worker thread is now polled; a dead one raises a named on-screen fault, is rebuilt in place with its queue migrated, and a second death within five minutes restarts the whole pipeline.
- **Honest status.** A backlog warning is suppressed while a hardware fault explains it — telling two people to speak more slowly because the earbuds are in their case is a lie. And a backlog that never drains with no fault to explain it means something is wedged invisibly, so it escalates rather than latching forever.
- **Nothing vanishes silently.** A turn that produces no speakable output plays a soft two-tone marker in the listener's ear and shows the untranslated text. Unverified translations are never spoken.

---

## Things that were tried and rejected

Recorded so nobody re-derives them.

- **Pinning each channel's recognition to its language as a bleed filter.** Whisper does not reject wrong-language audio — it confidently transcribes English as Spanish-sounding nonsense. The energy ratio is the only real gatekeeper for "whose speech is this."
- **Confidence-based language gating.** Whisper's language identification is perfect on clean clips and unreliable exactly where a gate would act: it labelled a real Spanish word as English at 0.36 confidence. Any threshold catching genuine errors would also discard real speech.
- **Group-cancel that holds playback until the speaker stops.** It delays the listener by the entire compute deficit. Display-only grouping gives the same clarity with no added latency.
- **A larger recognition model.** Anything above whisper `base` blows the latency budget on four cores. The bottleneck is compute, not memory — ~2 GB resident against 8 GB available.
- **SBC-XQ for channel isolation.** AirPods advertise only baseline SBC and AAC and renegotiate plain SBC even when XQ is forced. Plain SBC joint stereo tested clean by ear in the worst case (speech in one ear, near-silence in the other, both directions), so the requirement was retired.
