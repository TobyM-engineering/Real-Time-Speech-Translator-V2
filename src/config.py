"""Translator V2 pipeline configuration. Every number traces to the design doc
or a bench measurement — see CLAUDE.md before changing any of them."""

DJI_NODE = ("alsa_input.usb-DJI_Technology_Co.__Ltd._Wireless_Mic_Rx_"
            "<RECEIVER-SERIAL>-01.analog-stereo")

SR = 16000            # capture rate (PipeWire resamples the DJI's 48 k)
CHUNK = 512           # samples per block = 32 ms; Silero's native chunk

# Channel identity (Test A, bench-verified): FL = TX1 = black windscreen.
PERSON_A = "A"        # left channel,  black windscreen
PERSON_B = "B"        # right channel, grey windscreen

# VAD endpointing (fragment policy, measured)
VAD_MIN_SILENCE = 0.7
VAD_MIN_SPEECH = 0.25
VAD_MAX_SPEECH = 10.0   # force-split monologues; one whisper window, bounded wait.
                        # A forced split lands mid-sentence by construction —
                        # the merge window (stage 2) treats the seam as a fragment.
                        # NOTE: sherpa's silero max_speech_duration counts
                        # CONTINUOUS voiced runs and resets at breath pauses
                        # (bench: 17-26 s segments despite cap=10) — the pipeline
                        # enforces this wall-clock cap itself via
                        # is_speech_detected() + flush().

# Ratio is computed over speech-active frames only (own RMS >= this fraction of
# the segment's peak), not the whole span — pauses inside a segment sit at noise
# floor on both channels and dilute the ratio toward 0 (bench: dropped an easy
# case to +7.3 dB against the +6 threshold).
RATIO_ACTIVE_FRAC = 0.3

# Speaker arbitration (bench: wearers separate by 10-17 dB, measured 11.5)
RATIO_ACCEPT_DB = 6.0     # segment's own channel must dominate by this much
# |ratio| below RATIO_ACCEPT_DB → ambiguous → drop (D7)

# Anti-feedback gate (D6 starting guesses — tune with real bud-leak data)
GATE_MARGIN = 0.15        # s, slack around playback intervals
GATE_DISCARD_FRAC = 0.60  # overlap fraction above which the segment dies
GATE_MIN_KEEP = 0.5       # s of speech that must survive a trim to proceed

# Live LISTENING indicator: a channel's VAD onset lights its half only if the
# running cross-channel ratio (last ~0.5 s) is not strongly AGAINST it.
# Bleed measures -9..-14 dB, wearers +10..+17, both-talking ~0 — so suppressing
# below -3 dB kills bleed lighting instantly, keeps both halves lit when both
# people genuinely speak, and never waits for segment-end arbitration.
LIVE_RATIO_SUPPRESS_DB = 3.0
LIVE_RATIO_WINDOW_S = 0.5
LIVE_ONSET_HOLD_S = 0.30   # don't light either half until the ratio window has
                           # real speech frames. KNOWN ISSUE (accepted): with a
                           # mic an arm away FACING the speaker + AC/fan noise,
                           # occasional brief wrong-half flashes survive even
                           # ratio-since-onset; arbitration still drops the
                           # bleed, so it's cosmetic. Raised 0.15→0.30 (slower
                           # green) as the cheap mitigation — don't chase further.

# Mono-mode / self-check
SELFCHECK_CORR = 0.95     # L/R correlation above this at speech energy = suspect
SELFCHECK_RMS = 0.01      # full-scale RMS above this counts as "has signal"

# Fragment merge window (measured policy; see CLAUDE.md fragment section)
MERGE_SHORT_SEG = 1.5      # s: segments shorter than this are held for merge
MERGE_SHORT_WORDS = 4      # or with fewer ASR words than this
MERGE_HOLD = 0.8           # s to wait for a continuation
MERGE_HOLD_DANGLING = 2.0  # s when the text ends in a dangling function word
MERGE_MAX_GAP = 1.0        # s: max silence between segments that still merges
MERGE_MAX_TOTAL = 25.0     # s: stop chaining merges past this (whisper window is 30)
DANGLING_WORDS = {
    "and", "or", "but", "the", "a", "an", "to", "of", "with", "because",
    "if", "is", "are", "was", "were", "so", "that", "for", "in", "on", "at",
}

# Stage 2 only: with no MT/TTS downstream, the pending band shows the
# transcript for this long, then the turn closes.
STAGE2_BAND_SECONDS = 5.0

# Display grouping (Toby's spec): consecutive segments append into ONE band
# while speech continues; playback (stage 3) is untouched — chunks play as
# ready. A segment continues its group if it starts within this gap of the
# previous accepted segment, or the channel's VAD is still active.
GROUP_CONTINUE_GAP = 1.0
GROUP_QUIET_CLOSE = 1.2    # s of real quiet (VAD off, nothing pending) closes it

ASR_THREADS = 3
MT_THREADS = 3

# Stage 3 — audio out (D3 spike 2026-08-26: pw-play --raw stdin, pipe shrunk
# via F_SETPIPE_SZ 64 KB→16 KB = write-to-sink buffering ~100 ms)
AIRPODS_NODE = "bluez_output.10_B5_88_97_3B_1B.1"
OUT_RATE = 48000
OUT_FRAME = 960            # 20 ms mixer tick; blocking writes are the clock
PIPE_BYTES = 8192          # F_SETPIPE_SZ request (kernel rounds up)
PLAY_LATENCY = "50ms"
PLAY_HOLD_S = 1.0          # never start a turn's audio sooner than this after
                           # its transcript is shown (the cancel-window rule)
EAR_GAIN = {"A": 1.0, "B": 1.0}   # per-person digital gain (sink vol is shared)
DRAIN_SPEED = 1.03         # v1's inaudible backlog drain
DRAIN_BACKLOG_S = 2.0      # apply the 1.03x only past this much ear backlog
# Ears: LEFT = Person A, RIGHT = Person B (matches capture: FL = A's mic)

# D5 pause-request thresholds — AMENDED (Toby, 2026-08-26): the metric is now
# END-TO-END lag — from the end of an utterance to that content's audio
# reaching the sink. The original untranscribed-audio metric excluded the
# stages that actually grow unboundedly at 1.6x real time (MT + ear queue) and
# oscillated 10<->0 at chunk cadence while the listener fell minutes behind.
# Thresholds re-derived for the new signal: a single normal turn measures
# ~4.5-6.5 s end-to-end, so the old 8/15 would false-fire constantly.
#   meter 8 s  = visibly above a normal turn
#   SOFT 15 s  = roughly two turns queued; the speaker is outrunning compute
#   HARD 30 s  = the listener is half a minute behind; conversation is broken
# At the measured ~1.6x compute ratio, lag grows ~0.6 s per second of
# continuous speech: SOFT after ~25 s of sustained outrunning, HARD after ~50.
BACKLOG_METER_S = 8.0
BACKLOG_SOFT_S = 15.0
BACKLOG_HARD_S = 30.0
BACKLOG_POLL_MS = 500

MODELS = "<REPO-ROOT>/models"

