"""Speaker arbitration (design doc, D7 resolved): the energy-ratio test
authenticates a completed segment against the *other* channel.

Known limitation, stated in the design doc: this distinguishes the two wearers
from each other only. A bystander close to someone's chest mic is genuinely
dominant on that channel and passes like the wearer. Per-person mute is the
only defense."""
import math

from src import config

ACCEPT = "ACCEPT"
DROP_BLEED = "DROP_BLEED"          # the other wearer's voice, heard faintly
DROP_AMBIGUOUS = "DROP_AMBIGUOUS"  # similar coupling: overlap or mid-table voice
DROP_MUTED = "DROP_MUTED"
GATE_DISCARD = "GATE_DISCARD"      # our own TTS leaking bud → chest mic
GATE_TRIM = "GATE_TRIM"            # partial overlap; remainder would proceed
DROP_PTT_OTHER = "DROP_PTT_OTHER"  # the other half is holding to talk


def ratio_db(own_rms, other_rms):
    if other_rms <= 1e-9:
        return 99.0
    if own_rms <= 1e-9:
        return -99.0
    return 20.0 * math.log10(own_rms / other_rms)


def decide_ptt(muted):
    """Hold-to-talk (2026-08-26): the person has explicitly claimed their own
    mic, so the ratio test AND the anti-feedback gate are both bypassed —
    only mute still applies. No feedback loop is possible during a hold: the
    other channel is dropped outright for its duration, so TTS leak accepted
    here plays into the OTHER ear once, and its re-pickup lands on the
    dropped channel."""
    if muted:
        return DROP_MUTED, "person muted"
    return ACCEPT, "hold-to-talk (ratio and gate bypassed)"


def decide_ratio(own_rms, other_rms):
    """The wearer-authenticity ratio test alone (±RATIO_ACCEPT_DB).
    Split out 2026-08-28 so the completed trim path can re-test the
    surviving audio on its own energy."""
    r = ratio_db(own_rms, other_rms)
    if r >= config.RATIO_ACCEPT_DB:
        return ACCEPT, f"ratio {r:+.1f} dB"
    if r <= -config.RATIO_ACCEPT_DB:
        return DROP_BLEED, f"ratio {r:+.1f} dB — other wearer's voice"
    return DROP_AMBIGUOUS, f"ratio {r:+.1f} dB — simultaneous or mid-table"


def decide(person, own_rms, other_rms, muted, ledger, t0, t1):
    """Returns (decision, detail-string, keep) — keep is the (t0, t1) of
    the longest playback-free run for GATE_TRIM, else None. Order matters:
    mute is absolute, the gate runs before ratio (own-voice leak can be
    loud AND own-dominant), then the wearer-authenticity ratio test.
    2026-08-28: GATE_TRIM now carries real geometry from the ledger
    (the old fraction-estimate 'kept' was never acted on — survivors
    were silently discarded for the life of stage 3)."""
    if muted:
        return DROP_MUTED, "person muted", None

    frac, matched = ledger.overlap_detail(person, t0, t1)
    if frac >= config.GATE_DISCARD_FRAC:
        return GATE_DISCARD, (f"{frac:.0%} overlap with playback into own "
                              f"ear [ledger {matched}]"), None
    if frac > 0.0:
        runs = ledger.keep_intervals(person, t0, t1)
        ka, kb = max(runs, key=lambda r_: r_[1] - r_[0],
                     default=(t0, t0))
        if kb - ka < config.GATE_MIN_KEEP:
            return GATE_DISCARD, (f"{frac:.0%} overlap, longest clean run "
                                  f"only {kb - ka:.2f}s "
                                  f"[ledger {matched}]"), None
        return GATE_TRIM, (f"{frac:.0%} overlap [ledger {matched}]"), (ka, kb)

    dec, why = decide_ratio(own_rms, other_rms)
    return dec, why, None
