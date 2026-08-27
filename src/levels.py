"""Shared mic-level measurement and verdicts. tools/doctor.py and the UI
level panel both run exactly this code — one definition of "good".

Method (doctor's, unchanged): RMS over ~100 ms windows; speech level = the
90th percentile, background = the 10th (the room between your words), gap =
how far speech stands above the room. All thresholds trace to the bench:
-21..-26 dBFS is the verified band (Test A + the recovered-TX doctor run);
below -30 transcription degrades; above -15 speech peaks flatten against
full scale; a gap under 10 dB garbles words regardless of level. The Pi
cannot change the RX gain, so every advice string points at the DJI Mimo
app — and at the power-cycle-first rule from the transient-TX fault."""
import numpy as np

BAND = (-26.0, -21.0)   # verified good band, dBFS (quiet edge, loud edge)
GOOD_MIN = -30.0
FAR_MIN = -38.0
HOT = -15.0
MIN_GAP = 10.0          # dB speech must stand above the room
SPEECH_GAP = 6.0        # below this, assume nobody is talking


def db(x):
    return 20.0 * float(np.log10(max(float(x), 1e-6)))


def window_rms(x, win):
    """Per-window RMS of a mono float array (doctor's 100 ms windows)."""
    n = len(x) // win * win
    if n == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(x[:n].reshape(-1, win) ** 2, axis=1))


def stats(rms_windows):
    """(speech_db, background_db, gap_db) from a series of window RMS values."""
    if len(rms_windows) == 0:
        return -120.0, -120.0, 0.0
    lvl = db(np.percentile(rms_windows, 90))
    flr = db(np.percentile(rms_windows, 10))
    return lvl, flr, lvl - flr


def verdict(level_db, gap_db):
    """(state, headline, advice) in plain language."""
    if gap_db < SPEECH_GAP:
        return ("waiting", "NO SPEECH HEARD",
                "talk normally for a few seconds — if you were talking, the "
                "mic is barely hearing you: power-cycle the transmitters")
    if level_db > HOT:
        return ("hot", "TOO HOT",
                "lower the gain in the DJI Mimo app")
    if level_db < FAR_MIN:
        return ("far", "FAR TOO QUIET",
                "power-cycle the transmitters, then raise the gain in the "
                "DJI Mimo app")
    if gap_db < MIN_GAP:
        return ("noisy", "TOO NOISY",
                "speech barely above the room — mic closer to the mouth, or "
                "a quieter room; gain will not help")
    if level_db < GOOD_MIN:
        return ("quiet", "TOO QUIET",
                "raise the gain in the DJI Mimo app")
    return ("good", "GOOD", "leave the gain alone")
