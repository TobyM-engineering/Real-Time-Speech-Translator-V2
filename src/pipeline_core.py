"""Stage 2 bridge: one QObject connecting the AudioFrontend and AsrWorker to
the QML Signal UI. Single process (D1); worker callbacks arrive on worker
threads and leave as queued Qt signals; QML calls slots on the main thread.

Display grouping (Toby's spec): consecutive segments of continuous speech
append into ONE band per person — no LISTENING/TRANSLATING churn mid-paragraph
— while pipeline mechanics (per-chunk ASR, and in stage 3 per-chunk playback)
are untouched. Cancel means "stop everything not yet playing"; in stage 2
nothing plays, so cancel kills the whole group."""
import json
import threading
import time
from html import escape

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from src import config, levels
from src.asr_worker import AsrWorker
from src.frontend import AudioFrontend
from src.mt_worker import MtWorker
from src.playback import Playback
from src.tts_worker import TtsWorker

_OTHER = {config.PERSON_A: config.PERSON_B, config.PERSON_B: config.PERSON_A}
_GREY = "#9aa0a6"


class Bridge(QObject):
    logMsg = Signal(str)
    ready = Signal()
    stateChanged = Signal(str, str)        # person, state key
    groupText = Signal(str, str)           # person, accumulated transcript
    groupClosed = Signal(str, bool)        # person, cancelled
    langChanged = Signal(str, "QVariant")  # person, catalog entry
    overlapHint = Signal()
    backlogMeter = Signal(str, float)      # person, seconds untranscribed
    faultChanged = Signal(str)             # device fault banner ("" = clear)
    gapNotice = Signal(str, str)           # listener person, untranslated
                                           # source text (fall-through)
    levelUpdate = Signal(str)              # mic level panel data, JSON

    def __init__(self, catalog, downstream=False):
        super().__init__()
        self.downstream = downstream
        self._t0 = time.time()
        self._by_code = {e["code"]: e for e in catalog}
        self._lang = {config.PERSON_A: self._by_code["en"],
                      config.PERSON_B: self._by_code["es"]}
        self._lock = threading.Lock()
        self._pending = {config.PERSON_A: 0, config.PERSON_B: 0}
        self._speech = {config.PERSON_A: False, config.PERSON_B: False}
        self._state = {config.PERSON_A: "", config.PERSON_B: ""}
        self._groups = {config.PERSON_A: None, config.PERSON_B: None}
        self._close_timers = {}

        # ready = ASR warm AND (downstream only) both selected voices resident
        self._ready_asr = False
        self._ready_tts = not downstream
        self._ready_emitted = False
        self.frontend = AudioFrontend(on_log=self._log,
                                      on_speech=self._on_speech,
                                      on_segment=self._on_segment,
                                      on_ambiguous=self.overlapHint.emit)
        self.asr = AsrWorker(self.frontend.registry,
                             get_lang=lambda p: self._lang[p],
                             on_log=self._log,
                             on_transcript=self._on_transcript,
                             on_ready=self._on_asr_ready,
                             on_dropped=self._on_dropped)
        self._ear_active = {config.PERSON_A: False, config.PERSON_B: False}
        self._backlog_level = {config.PERSON_A: 0, config.PERSON_B: 0}
        self._meter = {config.PERSON_A: 0.0, config.PERSON_B: 0.0}
        self._meter_low_since = {config.PERSON_A: None, config.PERSON_B: None}
        self._lvl_timer = None   # mic level panel poll, runs only while open
        self.mt = self.tts = self.playback = None
        if downstream:
            self.mt = MtWorker(on_log=self._log,
                               on_translated=self._on_translated,
                               on_untranslated=self._on_untranslated)
            self.tts = TtsWorker(
                on_log=self._log, on_synth=self._on_synth,
                on_failed=self._on_untranslated,
                preload_codes=list(dict.fromkeys(
                    [self._lang[config.PERSON_A]["code"],
                     self._lang[config.PERSON_B]["code"]])),
                by_code=self._by_code,
                on_ready=self._on_tts_ready,
                active_codes=lambda: {self._lang[config.PERSON_A]["code"],
                                      self._lang[config.PERSON_B]["code"]})

    def _make_playback(self):
        return Playback(on_log=self._log, ledger=self.frontend.ledger,
                        stream_now=self.frontend.stream_now,
                        on_play_start=self._on_play_start,
                        on_ear_active=self._on_ear_active)

    def restart_playback(self):
        """BT recovery (supervisor): rebuild the output stream, keep fresh
        queued audio, drop stale, reset listener states."""
        old, self.playback = self.playback, None
        items = {}
        if old:
            try:
                items = old.take_fresh_items()
                old.stop()
            except Exception:
                pass
        with self._lock:
            for p in self._ear_active:
                self._ear_active[p] = False
            self._recompute()
        self.playback = self._make_playback()
        self.playback.adopt_items(items)
        self.playback.start()

    def _on_asr_ready(self):
        self._ready_asr = True
        self._maybe_ready()

    def _on_tts_ready(self):
        self._ready_tts = True
        self._maybe_ready()

    def _maybe_ready(self):
        with self._lock:
            if not (self._ready_asr and self._ready_tts) \
                    or self._ready_emitted:
                return
            self._ready_emitted = True
        self._log("READY pipeline ready (ASR warm; selected voices resident)")
        self.ready.emit()

    def set_fault(self, msg):
        if msg != getattr(self, "_fault", None):
            self._fault = msg
            if msg:
                self._log(f"FAULT {msg}")
            self.faultChanged.emit(msg)

    def start(self):
        self.asr.start()
        self.frontend.start()
        if self.downstream:
            self.mt.start()
            self.tts.start()
            self.playback = self._make_playback()
            self.playback.start()
            from src.supervisor import Supervisor
            self.supervisor = Supervisor(self)
            self.supervisor.start()
        self._poll = QTimer(self)
        self._poll.setInterval(config.BACKLOG_POLL_MS)
        self._poll.timeout.connect(self._poll_backlog)
        self._poll.start()
        a, b = self._lang[config.PERSON_A], self._lang[config.PERSON_B]
        self._log(f"LANG A={a['code']}({a['asr']})  B={b['code']}({b['asr']})")
        if self.mt:
            self.mt.preload_pair(a, b)   # both directions, on the MT thread
        self._log(f"stage {'3' if self.downstream else '2'} up — capture live")
        with self._lock:
            self._recompute()

    _PENDING_STATES = ("captured", "transcribed", "translated", "synthesized")

    def _lag_for(self, p):
        """D5 metric, as amended: seconds from the end of this person's oldest
        outstanding utterance to now — content is 'arrived' once its audio
        starts reaching the sink (play start), and drops/cancels/merges leave
        the set via their terminal states."""
        states = self._PENDING_STATES if self.downstream else ("captured",)
        now_s = self.frontend.stream_now()   # same clock as turn spans
        pend = [t.t1 for t in self.frontend.registry.snapshot()
                if t.person == p and not t.cancelled and t.state in states]
        return max(0.0, now_s - min(pend)) if pend else 0.0

    def _poll_backlog(self):
        for p in (config.PERSON_A, config.PERSON_B):
            # safety net: a turn that dies without a bridge callback
            # (mt_dropped / tts_failed) lowers _inflight with no event, so
            # re-arm the close countdown if none is pending
            t = self._close_timers.get(p)
            if self._groups[p] is not None and (t is None or not t.is_alive()):
                self._arm_close_check(p)
            s = self._lag_for(p)
            lvl = (2 if s >= config.BACKLOG_HARD_S
                   else 1 if s >= config.BACKLOG_SOFT_S else 0)
            with self._lock:
                if lvl != self._backlog_level[p]:
                    self._backlog_level[p] = lvl
                    names = {0: "clear", 1: "SOFT pause-request",
                             2: "HARD pause-request"}
                    self._log(f"D5   {p} backlog {s:.0f}s -> {names[lvl]}")
                    self._recompute()
            # latched meter, round 2 (bench: the untranscribed metric is
            # BINARY at 10 s chunk cadence — 10 then 0 — so any time-based
            # clear window blanks mid-monologue). The latch now clears only
            # at genuine idleness: group closed, nothing pending, metric low.
            now = time.time()
            with self._lock:
                busy = (self._groups[p] is not None or self._pending[p] > 0
                        or self._speech[p])
            if s >= config.BACKLOG_METER_S:
                self._meter[p] = s
                self._meter_low_since[p] = None
            elif self._meter[p] > 0:
                if s > 1.0:
                    self._meter[p] = s
                    self._meter_low_since[p] = None
                elif busy:
                    self._meter_low_since[p] = None   # hold last value
                elif self._meter_low_since[p] is None:
                    self._meter_low_since[p] = now
                elif now - self._meter_low_since[p] >= 3.0:
                    self._meter[p] = 0.0
            if abs(self._meter[p] - getattr(self, "_meter_logged", {}).get(p, -9)) >= 1.0:
                self._meter_logged = getattr(self, "_meter_logged", {})
                self._meter_logged[p] = self._meter[p]
                self._log(f"D5m  {p} meter {self._meter[p]:.0f}s "
                          f"(raw {s:.0f}s, busy={busy})")
            self.backlogMeter.emit(p, self._meter[p])

    def stop(self):
        self.frontend.stop()
        self.asr.stop()
        for w in (self.mt, self.tts, self.playback,
                  getattr(self, "supervisor", None)):
            if w:
                w.stop()

    # -- state machine (call with lock held) ----------------------------
    def _recompute(self):
        for p in (config.PERSON_A, config.PERSON_B):
            if self.frontend.muted[p]:
                s = "muted"
            elif self._backlog_level[p] == 2:
                s = "pause_hard"    # D5: hard level always takes the half
            elif self._ear_active[p]:
                s = "speaking"      # audio playing into this person's ear
            elif self._backlog_level[p] == 1 and self._groups[p] is None:
                s = "pause_soft"    # D5: soft flips state only outside a group
            elif self._pending[p] > 0 or self._groups[p] is not None:
                s = "translating"
            elif self._speech[p]:
                s = "listening"
            else:
                s = "ready"
            if s != self._state[p]:
                self._state[p] = s
                self.stateChanged.emit(p, s)

    def _finish(self, turn):
        """Exactly-once pending decrement per turn (lock held by caller)."""
        if turn.closed:
            return False
        turn.closed = True
        if self._pending[turn.person] > 0:
            self._pending[turn.person] -= 1
        return True

    # -- group close: after real quiet with nothing in flight ------------
    def _inflight(self, p):
        """Turns of this person still on their way to the ear. The close
        check and the cancel sweep share the D5 pending-state set: a turn is
        in flight until its audio has STARTED playing (or it hit a terminal
        state). The 2026-08-27 cancel bug was exactly this set being ignored:
        groups closed on the ASR-only pending counter while MT/TTS/queued
        audio still trailed, and cancel found nothing to cancel."""
        if not self.downstream:
            return 0
        return sum(1 for t in self.frontend.registry.snapshot()
                   if t.person == p and not t.cancelled
                   and t.state in self._PENDING_STATES)

    def _arm_close_check(self, person):
        t = self._close_timers.pop(person, None)
        if t:
            t.cancel()
        with self._lock:
            idle = (self._groups[person] is not None
                    and self._pending[person] == 0
                    and not self._speech[person]
                    and self._inflight(person) == 0)
        if idle:
            t = threading.Timer(config.GROUP_QUIET_CLOSE,
                                self._try_close, args=(person,))
            t.daemon = True
            self._close_timers[person] = t
            t.start()

    def _try_close(self, person):
        with self._lock:
            if (self._groups[person] is None or self._pending[person] > 0
                    or self._speech[person] or self._inflight(person) > 0):
                return
            self._groups[person] = None
            self._recompute()
        self.groupClosed.emit(person, False)
        self._log(f"GRP  {person} group closed (quiet)")

    # -- worker-thread callbacks ----------------------------------------
    def _log(self, msg):
        self.logMsg.emit(f"[{time.time()-self._t0:7.2f}] {msg}")

    def _on_speech(self, person, active):
        with self._lock:
            self._speech[person] = active
            self._recompute()
        self._arm_close_check(person)

    def _on_segment(self, turn, audio, overlap, continues):
        p = turn.person
        with self._lock:
            g = self._groups[p]
            if g is not None and not continues:
                # stale open group from earlier speech: close it as a new,
                # unrelated utterance begins
                self._groups[p] = None
                self.groupClosed.emit(p, False)
                g = None
            if g is None:
                g = {"texts": [], "turns": set(), "played": set()}
                self._groups[p] = g
            g["turns"].add(turn.turn_id)
            self._pending[p] += 1
            self._recompute()
        if overlap:
            self.overlapHint.emit()
        self.asr.submit(turn, audio)

    def _styled_locked(self, g):
        out = []
        dead = g.get("dead", set())
        for tid, txt in g["texts"]:
            e = escape(txt)
            if tid in dead:      # discarded: the speaker sees it undelivered
                out.append(f'<s><font color="{_GREY}">{e}</font></s>')
            elif tid in g["played"]:
                out.append(f'<font color="{_GREY}">{e}</font>')
            else:
                out.append(e)
        return " ".join(out)

    def _on_transcript(self, turn, text):
        p = turn.person
        turn.t_shown_wall = time.time()
        turn.src_text = text   # kept for the fall-through gap notice
        with self._lock:
            g = self._groups[p]
            if g is None or turn.turn_id not in g["turns"]:
                # decode finished after its group closed/cancelled: show as
                # its own short-lived group
                g = {"texts": [], "turns": {turn.turn_id}, "played": set()}
                self._groups[p] = g
            g["texts"].append((turn.turn_id, text))
            full = self._styled_locked(g)
            self._finish(turn)
            self._recompute()
        turn.notes.append(f"t_shown={time.time()-self._t0:.2f}")
        self.groupText.emit(p, full)
        self._arm_close_check(p)
        if self.downstream and not turn.cancelled:
            other = _OTHER[p]
            self.mt.submit(turn, text, self._lang[p], self._lang[other], other)

    def _on_translated(self, turn, text, tgt_entry, ear):
        self.tts.submit(turn, text, tgt_entry, ear)

    def _on_synth(self, turn, ear, samples):
        self.playback.enqueue(ear, turn, samples,
                              not_before=turn.t_shown_wall + config.PLAY_HOLD_S)

    def _on_play_start(self, turn):
        p = turn.person
        with self._lock:
            g = self._groups[p]
            if g is not None and turn.turn_id in g["turns"]:
                g["played"].add(turn.turn_id)
                full = self._styled_locked(g)
            else:
                full = None
        if full is not None:
            self.groupText.emit(p, full)
        # play-start is the moment the LAST in-flight turn can leave the
        # pending set — the close countdown must get a chance to arm here
        self._arm_close_check(p)

    def _on_untranslated(self, turn, ear):
        """Fall-through (design approved 2026-08-27): the turn yielded no
        audio — make the gap VISIBLE, never speak an unverified guess. The
        listener hears a soft tone and sees the untranslated source; the
        speaker's band strikes the sentence through."""
        text = getattr(turn, "src_text", "") or ""
        self._log(f"GAP  turn#{turn.turn_id} signalled to ear {ear} "
                  f"(untranslated source shown)")
        if self.playback:
            self.playback.enqueue_gap(ear)
        self.gapNotice.emit(ear, text)
        with self._lock:
            g = self._groups[turn.person]
            if g is not None and turn.turn_id in g["turns"]:
                g.setdefault("dead", set()).add(turn.turn_id)
                full = self._styled_locked(g)
            else:
                full = None
        if full is not None:
            self.groupText.emit(turn.person, full)

    def _on_ear_active(self, person, active):
        with self._lock:
            self._ear_active[person] = active
            self._recompute()

    def _on_dropped(self, turn, reason):
        with self._lock:
            done = self._finish(turn)
            self._recompute()
        if done:
            self._log(f"TURN turn#{turn.turn_id} dropped ({reason})")
        self._arm_close_check(turn.person)

    # -- QML slots (main thread) ----------------------------------------
    @Slot(str)
    def cancelGroup(self, person):
        """Stop everything of this person's that is not yet playing —
        whether or not the group object still exists (it can close under a
        still-visible band), and even while one sentence is already
        sounding: that one finishes, everything queued behind it dies."""
        t = self._close_timers.pop(person, None)
        if t:
            t.cancel()
        killed, cut = 0, 0
        with self._lock:
            g = self._groups[person]
            was_open = g is not None
            group_ids = set(g["turns"]) if was_open else set()
            for t2 in self.frontend.registry.snapshot():
                if t2.person != person or t2.cancelled:
                    continue
                if t2.state in self._PENDING_STATES:
                    self.frontend.registry.cancel(t2.turn_id)
                    killed += 1          # genuinely stopped before playing
                elif t2.state == "playing":
                    self.frontend.registry.cancel(t2.turn_id)
                    cut += 1             # mixer cuts its audio mid-chunk
                elif t2.turn_id in group_ids:
                    pass                 # already dead — not counted
            self._groups[person] = None
            self._recompute()
        bits = []
        if killed:
            bits.append(f"{killed} queued turn(s) killed")
        if cut:
            bits.append(f"{cut} playing turn(s) cut")
        if not bits:
            bits.append("nothing to cancel")
        self._log(f"CTRL cancel {person}: {', '.join(bits)}"
                  f"{'' if was_open else ' (group was already closed)'}")
        self.groupClosed.emit(person, True)

    @Slot(str, bool)
    def setMuted(self, person, value):
        self.frontend.set_muted(person, value)
        with self._lock:
            self._recompute()

    @Slot(str, bool)
    def setHold(self, person, value):
        """Hold-to-talk from the touch layer. Mute wins over a hold."""
        if value and self.frontend.muted[person]:
            self._log(f"CTRL {person} hold-to-talk refused (muted)")
            return
        self.frontend.set_ptt(person, value)

    @Slot(str, str)
    def setLanguage(self, person, code):
        entry = self._by_code.get(code)
        if not entry:
            return
        self._lang[person] = entry
        self._log(f"CTRL {person} language -> {code} ({entry['name']})")
        if self.tts:   # load the voice NOW, on the TTS thread — not on the
            self.tts.preload_voice(entry)   # first turn's synth path
        if self.mt:    # and the opus pair for the new language combination
            self.mt.preload_pair(self._lang[config.PERSON_A],
                                 self._lang[config.PERSON_B])
        self.langChanged.emit(person, entry)

    @Slot(str, result="QVariant")
    def getLang(self, person):
        return self._lang[person]

    # -- mic level panel (doctor's measurement, live) --------------------
    @Slot(bool)
    def setLevelPanel(self, open_):
        if open_:
            if self._lvl_timer is None:
                self._lvl_timer = QTimer(self)
                self._lvl_timer.setInterval(100)
                self._lvl_timer.timeout.connect(self._poll_levels)
            self._lvl_timer.start()
        elif self._lvl_timer is not None:
            self._lvl_timer.stop()
        self._log(f"LEVEL panel {'open' if open_ else 'closed'}")

    def _poll_levels(self):
        # last ~5 s of the frontend's 32 ms per-block RMS (post-gain — what
        # the models hear). Tail slice is atomic under the GIL; the frontend
        # only appends/prunes in place.
        tail = self.frontend.energy[-156:]
        out = {}
        for key, idx in (("a", 1), ("b", 2)):
            r = np.array([e[idx] for e in tail], dtype=np.float64)
            # regroup 3 blocks -> ~96 ms windows = doctor's 100 ms method
            n = len(r) // 3 * 3
            w = (np.sqrt((r[:n].reshape(-1, 3) ** 2).mean(axis=1))
                 if n else r[:0])
            lvl, flr, gap = levels.stats(w)
            inst = levels.db(np.sqrt((r[-4:] ** 2).mean())) if len(r) else -120.0
            out[key] = {"inst": round(inst, 1), "level": round(lvl, 1),
                        "floor": round(flr, 1), "gap": round(gap, 1)}
        best = max(("a", "b"), key=lambda k: out[k]["level"])
        state, headline, advice = levels.verdict(out[best]["level"],
                                                 out[best]["gap"])
        out.update(state=state, headline=headline, advice=advice)
        self.levelUpdate.emit(json.dumps(out))
