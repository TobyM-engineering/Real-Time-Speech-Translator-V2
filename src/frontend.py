"""AudioFrontend: the verified stage-1 front half as a reusable component —
capture, per-channel VAD, wall-clock segment cap, speaker arbitration, the
anti-feedback gate, turn creation. Stage 1's CLI and stage 2's pipeline both
run exactly this code.

Callbacks (all invoked on the frontend's processing thread — keep them cheap;
hand heavy work to a queue):
  on_log(msg)
  on_speech(person, active_bool)        # live VAD state for the UI
  on_segment(turn, audio_f32, overlap)  # ACCEPTed segments only
"""
import queue
import threading
import time

import numpy as np

from src import arbitration, config
from src.capture import CaptureThread
from src.gate import GateLedger
from src.turns import TurnRegistry


class AudioFrontend:
    def __init__(self, on_log, on_speech=None, on_segment=None):
        self.on_log = on_log
        self.on_speech = on_speech or (lambda *a: None)
        self.on_segment = on_segment or (lambda *a: None)
        self.ledger = GateLedger()
        self.registry = TurnRegistry()
        self.energy = []   # (sample_idx, fl_rms, fr_rms) per 32 ms block,
                           # post-gain; appended by the processing thread,
                           # read (atomic tail slices only) by the UI level
                           # panel
        self.muted = {config.PERSON_A: False, config.PERSON_B: False}
        self.ptt = {config.PERSON_A: False, config.PERSON_B: False}
        self._ptt_ivs = {config.PERSON_A: [], config.PERSON_B: []}
        self.error = None
        self._blocks = queue.Queue(maxsize=256)
        self._drops = 0            # capture blocks discarded on overflow
        self._drop_logged = 0.0
        self._stop = threading.Event()
        self._cap = None
        self._thread = None
        self._sample_base = 0
        self.start_wall = None       # capture epoch, for stream/wall alignment
        self.cap_started_wall = None  # last (re)open time — the supervisor's
                                      # died-instantly vs died-after-running test

    # -- controls (any thread) ------------------------------------------
    def set_muted(self, person, value):
        self.muted[person] = bool(value)
        self.on_log(f"CTRL {person} muted={bool(value)}")

    def set_ptt(self, person, active):
        """Hold-to-talk (UI). Hold spans are kept as stream-time intervals so
        a segment that STRADDLES press or release still counts as held — the
        closing segment of a hold always arrives ~0.7 s AFTER release, when
        the endpoint silence completes."""
        now_s = (time.time() - self.start_wall) if self.start_wall else 0.0
        ivs = self._ptt_ivs[person]
        if active and not self.ptt[person]:
            ivs.append([now_s, None])
            del ivs[:-6]
        elif not active and self.ptt[person] and ivs and ivs[-1][1] is None:
            ivs[-1][1] = now_s
        self.ptt[person] = bool(active)
        self.on_log(f"CTRL {person} hold-to-talk={'ON' if active else 'off'}")

    def ptt_covers(self, person, a, b):
        """Did [a, b] (stream seconds) overlap any of this person's holds?"""
        for s, e in list(self._ptt_ivs[person]):
            if a < (e if e is not None else float("inf")) and b > s:
                return True
        return False

    # -- lifecycle ------------------------------------------------------
    def start(self):
        import sherpa_onnx as so

        def make_vad():
            vc = so.VadModelConfig()
            vc.silero_vad.model = f"{config.MODELS}/silero_vad.onnx"
            vc.silero_vad.min_silence_duration = config.VAD_MIN_SILENCE
            vc.silero_vad.min_speech_duration = config.VAD_MIN_SPEECH
            if hasattr(vc.silero_vad, "max_speech_duration"):
                vc.silero_vad.max_speech_duration = config.VAD_MAX_SPEECH
            vc.sample_rate = config.SR
            return so.VoiceActivityDetector(vc, buffer_size_in_seconds=180)

        self._vads = {config.PERSON_A: make_vad(), config.PERSON_B: make_vad()}
        self.start_wall = time.time()
        self.cap_started_wall = self.start_wall
        self._cap = CaptureThread(self._on_block)
        self._cap.start()
        self._thread = threading.Thread(target=self._run, name="frontend",
                                        daemon=True)
        self._thread.start()
        self.on_log(f"CAP  digital gain +{config.CAPTURE_GAIN_DB:.1f} dB "
                    f"(x{10**(config.CAPTURE_GAIN_DB/20):.2f}) applied at capture")

    def capture_alive(self):
        return self._cap is not None and self._cap.is_alive() and not self.error

    def restart_capture(self):
        """Reopen the mic after a capture death (supervisor only). The sample
        counter is re-based to wall time so stream-time stays continuous for
        the gate ledger and turn spans."""
        try:
            self._cap.stop()
        except Exception:
            pass
        self._sample_base = int((time.time() - self.start_wall) * config.SR)
        self.error = None
        self.cap_started_wall = time.time()
        self._cap = CaptureThread(self._on_block)
        self._cap.start()
        self.on_log(f"CAP  reopened (stream re-based to "
                    f"{self._sample_base/config.SR:.1f}s)")

    def stop(self):
        self._stop.set()
        if self._cap:
            self._cap.stop()

    def _on_block(self, block, sample_index):
        try:
            self._blocks.put_nowait((block, sample_index + self._sample_base))
        except queue.Full:
            # capture never blocks — but discarded audio must never be
            # silent. Counted always; logged at most once per second with
            # the running total, so every dropped block is accounted for.
            self._drops += 1
            now = time.time()
            if now - self._drop_logged >= 1.0:
                self._drop_logged = now
                self.on_log(f"CAP  OVERFLOW: {self._drops} blocks "
                            f"({self._drops * config.CHUNK / config.SR:.1f}s "
                            f"of audio) dropped since start — processing is "
                            f"not keeping up")

    # -- processing thread ---------------------------------------------
    def _run(self):
        A, B = config.PERSON_A, config.PERSON_B
        other = {A: B, B: A}
        energy = self.energy   # in-place mutations only — shared with the UI
        ENERGY_KEEP = int(180 * config.SR / config.CHUNK)
        raw_tail = {0: [], 1: []}
        TAIL_BLOCKS = int(3 * config.SR / config.CHUNK)
        selfcheck_done = False
        selfcheck_next = 3 * config.SR
        deferred_logged = False
        speech_start = {A: None, B: None}
        speech_state = {A: False, B: False}
        forced_next = {A: False, B: False}
        raw_since = {A: None, B: None}
        last_accept = {A: (-99.0, -99.0), B: (-99.0, -99.0)}

        def seg_rms(person, s0, s1):
            own_i, oth_i = (1, 2) if person == A else (2, 1)
            pairs = [(e[own_i], e[oth_i]) for e in energy if s0 <= e[0] < s1]
            if not pairs:
                return 0.0, 0.0
            peak = max(p[0] for p in pairs)
            act = [p for p in pairs
                   if p[0] >= config.RATIO_ACTIVE_FRAC * peak] or pairs
            return (float(np.sqrt(np.mean([p[0] ** 2 for p in act]))),
                    float(np.sqrt(np.mean([p[1] ** 2 for p in act]))))

        while not self._stop.is_set():
            if self._cap.error:
                self.error = self._cap.error
                self.on_log(f"FATAL capture: {self._cap.error}")
                return
            try:
                block, s0 = self._blocks.get(timeout=0.25)
            except queue.Empty:
                continue
            frames = np.frombuffer(block, dtype=np.int16).reshape(-1, 2)
            g = 10.0 ** (config.CAPTURE_GAIN_DB / 20.0)
            fl = np.clip(frames[:, 0].astype(np.float32) / 32768.0 * g, -1, 1)
            fr = np.clip(frames[:, 1].astype(np.float32) / 32768.0 * g, -1, 1)
            energy.append((s0, float(np.sqrt(np.mean(fl ** 2))),
                           float(np.sqrt(np.mean(fr ** 2)))))
            if len(energy) > ENERGY_KEEP:
                del energy[:1000]
            for ch, x in ((0, fl), (1, fr)):
                raw_tail[ch].append(x)
                if len(raw_tail[ch]) > TAIL_BLOCKS:
                    raw_tail[ch].pop(0)

            if not selfcheck_done and s0 >= selfcheck_next:
                l = np.concatenate(raw_tail[0]); r = np.concatenate(raw_tail[1])
                rl = float(np.sqrt(np.mean(l ** 2)))
                rr = float(np.sqrt(np.mean(r ** 2)))
                if max(rl, rr) < config.SELFCHECK_RMS:
                    if not deferred_logged:
                        self.on_log("CHECK channels quiet — mono-mode check "
                                    "waits for speech")
                        deferred_logged = True
                    selfcheck_next = s0 + 2 * config.SR
                else:
                    corr = float(np.corrcoef(l, r)[0, 1]) if len(l) > 1 else 0.0
                    if corr > config.SELFCHECK_CORR and \
                            min(rl, rr) > 0.5 * max(rl, rr):
                        self.on_log(f"CHECK *** SUSPECT MONO MODE: corr "
                                    f"{corr:.3f} — double-press the receiver's "
                                    f"link button ***")
                    else:
                        self.on_log(f"CHECK channels distinct (corr {corr:.2f})"
                                    f" — stereo mode OK")
                    selfcheck_done = True

            self._vads[A].accept_waveform(fl)
            self._vads[B].accept_waveform(fr)

            recent = energy[-20:]
            for person in (A, B):
                v = self._vads[person]
                raw = v.is_speech_detected()
                if raw and raw_since[person] is None:
                    raw_since[person] = s0
                elif not raw:
                    raw_since[person] = None
                # live ratio over frames SINCE THIS CHANNEL'S ONSET (capped at
                # 0.5 s): undiluted by pre-speech silence, so bleed onset while
                # the other person is mid-utterance suppresses on the very
                # first evaluation instead of after the window fills
                start = raw_since[person] if raw_since[person] is not None else s0
                start = max(start,
                            s0 - int(config.LIVE_RATIO_WINDOW_S * config.SR))
                frames = [e for e in recent if e[0] >= start] or recent[-3:]
                own_i, oth_i = (1, 2) if person == A else (2, 1)
                own = float(np.sqrt(np.mean([e[own_i] ** 2 for e in frames])))
                oth = float(np.sqrt(np.mean([e[oth_i] ** 2 for e in frames])))
                settled = raw_since[person] is not None and \
                    (s0 - raw_since[person]) / config.SR >= config.LIVE_ONSET_HOLD_S
                # during a hold, trust the channel: light instantly, no
                # ratio suppression, no onset hold
                lit = raw and (self.ptt[person]
                               or (settled
                                   and arbitration.ratio_db(own, oth)
                                   > -config.LIVE_RATIO_SUPPRESS_DB))
                if lit != speech_state[person]:
                    speech_state[person] = lit
                    self.on_speech(person, lit)
                # the segment cap tracks RAW vad activity — bleed segments
                # still get capped and then dropped by arbitration
                if raw:
                    if speech_start[person] is None:
                        speech_start[person] = s0
                    elif (s0 - speech_start[person]) / config.SR >= \
                            config.VAD_MAX_SPEECH:
                        v.flush()
                        forced_next[person] = True
                        self.on_log(f"SPLIT ch={person} forced at "
                                    f"{config.VAD_MAX_SPEECH:.0f}s")
                        speech_start[person] = None
                else:
                    speech_start[person] = None

            for person in (A, B):
                v = self._vads[person]
                while not v.empty():
                    seg = v.front
                    a = seg.start / config.SR
                    b = a + len(seg.samples) / config.SR
                    audio = np.asarray(seg.samples, dtype=np.float32)
                    v.pop()
                    own, oth = seg_rms(person, int(a * config.SR),
                                       int(b * config.SR))
                    if self.ptt_covers(person, a, b):
                        dec, why = arbitration.decide_ptt(self.muted[person])
                    elif self.ptt_covers(other[person], a, b):
                        dec, why = (arbitration.DROP_PTT_OTHER,
                                    "other half is holding to talk")
                    else:
                        dec, why = arbitration.decide(
                            person, own, oth, self.muted[person],
                            self.ledger, a, b)
                    stamp = (f"SEG  ch={person} {b-a:4.1f}s "
                             f"span {a:6.2f}-{b:6.2f}")
                    if dec == arbitration.ACCEPT:
                        t = self.registry.new_turn(person, a, b)
                        t.forced_split = forced_next[person]
                        forced_next[person] = False
                        oa, ob = last_accept[other[person]]
                        overlap = a < ob and b > oa
                        prev_end = last_accept[person][1]
                        continues = (a - prev_end) <= config.GROUP_CONTINUE_GAP \
                            or v.is_speech_detected()
                        self.on_log(f"{stamp}  {why} -> ACCEPT turn#{t.turn_id}"
                                    + ("  [forced-split seam]"
                                       if t.forced_split else "")
                                    + ("  [OVERLAP]" if overlap else ""))
                        last_accept[person] = (a, b)
                        self.on_segment(t, audio, overlap, continues)
                    else:
                        forced_next[person] = False
                        self.on_log(f"{stamp}  -> {dec}: {why}")
