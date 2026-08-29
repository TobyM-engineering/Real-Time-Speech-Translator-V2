"""T6 supervisor (stage 4): v1's proven recovery logic, ported to v2 hardware.

- Startup: wait for an actual Bluetooth controller before monitoring (v1: the
  radio can come up after us at boot).
- Capture: reopen on death, distinguishing "died instantly on open" (bad node /
  receiver unplugged — long backoff, distinct message) from "died after
  running" (transient — fast reopen). (v1's mic reopen logic.)
- Playback/BT: detect write failures AND stalls (writes stopped flowing while
  audio queued), poll actual connection state independently, and escalate
  rather than retrying identically forever (v1's watchdog lesson: 43 identical
  attempts in 11 minutes). v2 ladder for the ONBOARD radio (v1's final rung
  was USB unbind/rebind — there is no USB radio here):
    L0 bluetoothctl connect  →  L1 adapter power-cycle + connect
    →  L2 systemctl restart bluetooth (NEEDS ROOT — logged loudly if denied;
        a polkit rule or sudoers entry from Toby makes it available)
  On success: rebuild the playback stream, drop stale queued audio (>10 s),
  clear the fault.
- Thermals: poll vcgencmd; ≥80 °C surfaces on the fault strip (throttle costs
  +37% on every stage), clears below 78.
"""
import subprocess
import threading
import time

from src import battery, config

_MAC = "<EARBUDS-MAC>"


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return 1, str(e)


class Supervisor(threading.Thread):
    def __init__(self, bridge):
        super().__init__(name="supervisor", daemon=True)
        self.b = bridge
        self._stopping = threading.Event()
        self._cap_failures = 0
        self._cap_next_try = 0.0
        self._bt_attempt = 0
        self._bt_next_try = 0.0
        self._therm_next = 0.0
        self._bt_poll_next = 0.0
        self._therm_fault = False
        self._drift_logged = 0.0
        self._drift_next = 0.0
        self._batt_next = 0.0
        self._batt_last = None
        self._batt_fault = False
        self._workers_next = 0.0
        self._worker_restarting = set()
        self._worker_last_restart = {}

    def stop(self):
        self._stopping.set()

    def log(self, msg):
        self.b._log(f"SUP  {msg}")

    # ------------------------------------------------------------------
    def run(self):
        self._wait_for_controller()
        self._start_bt_monitor()
        while not self._stopping.is_set():
            time.sleep(2.0)
            now = time.time()
            self._check_capture(now)
            self._check_bt(now)
            self._check_thermal(now)
            self._check_drift(now)
            self._check_battery(now)
            self._check_workers(now)

    # -- worker-thread liveness (2026-08-28: the ASR thread died on an
    # IndexError and the device sat "translating…" forever — a silent
    # dead thread is the worst failure mode this device has) -------------
    def _check_workers(self, now):
        if now < self._workers_next:
            return
        self._workers_next = now + 5.0
        for name, w in self.b.worker_threads().items():
            if w is None or w.is_alive() or name in self._worker_restarting:
                continue
            self.log(f"FAULT worker thread '{name}' is DEAD")
            last = self._worker_last_restart.get(name, -1e9)
            if now - last < config.WORKER_RESTART_WINDOW_S:
                self._pipeline_restart(
                    f"worker '{name}' died again {now - last:.0f}s "
                    f"after its restart")
                return
            self._worker_last_restart[name] = now
            self._worker_restarting.add(name)
            self.b.set_fault(f"Translator fault ({name}) — recovering…")
            threading.Thread(target=self._restart_worker, args=(name,),
                             name=f"restart-{name}", daemon=True).start()

    def _restart_worker(self, name):
        ok = False
        try:
            ok = self.b.restart_worker(name)
        except Exception as e:
            self.log(f"worker restart ({name}) raised: {e!r}")
        finally:
            self._worker_restarting.discard(name)
        if ok:
            self.log(f"worker '{name}' RESTARTED and alive")
            if getattr(self.b, "_fault", "").startswith("Translator fault"):
                self.b.set_fault("")
        else:
            self._pipeline_restart(f"worker '{name}' restart failed")

    def _pipeline_restart(self, reason):
        """Last rung: replace the whole process in place. exec preserves
        the PID, the launcher's log redirect, and the environment; the
        capture child and output pipe are stopped first so the new
        process doesn't fight orphans for the devices."""
        self.log(f"FAULT escalating to PIPELINE RESTART: {reason}")
        self.b.set_fault("Restarting translator…")
        for closer in (lambda: self.b.frontend.stop(),
                       lambda: self.b.playback and self.b.playback.stop()):
            try:
                closer()
            except Exception:
                pass
        time.sleep(0.5)
        self._exec_restart()

    def _exec_restart(self):
        import os
        import sys
        os.chdir("<REPO-ROOT>")
        os.execv(sys.executable, [sys.executable, "-m", "src.stage3_main"])

    # -- UPS HAT (E) fuel gauge (verified live 2026-08-28) ---------------
    def _check_battery(self, now):
        if now < self._batt_next:
            return
        self._batt_next = now + 10.0
        r = battery.read()
        if r is None:
            self.b.set_battery(-1, "")   # no gauge — indicator hides
            return
        self.b.set_battery(r["percent"], r["state"])
        key = (r["percent"] // 5, r["state"])
        if key != self._batt_last:
            self._batt_last = key
            self.log(f"BATT {r['percent']}% {r['state']} "
                     f"pack {r['pack_mv']/1000:.2f}V "
                     f"min-cell {r['cell_min_mv']}mV")
        # Waveshare's own threshold: any cell under 3150 mV while
        # discharging — surface it well before their 60 s auto-cutoff
        if r["low"] and r["state"] == "discharging" and not self._batt_fault:
            self._batt_fault = True
            self.log(f"BATTERY LOW: min cell {r['cell_min_mv']}mV")
            self.b.set_fault("Battery low — charge now")
        elif self._batt_fault and (r["cell_min_mv"] > 3300
                                   or "charging" in r["state"]):
            self._batt_fault = False
            self.log("battery recovered")
            self.b.set_fault("")

    # -- stream-vs-wall drift monitor (audit 2026-08-28: a one-time +9.8 s
    # surge appeared under background I/O load and was invisible). All
    # timing-critical paths now run on the stream clock, so this is
    # telemetry: it makes capture anomalies visible instead of silent.
    def _check_drift(self, now):
        if now < self._drift_next:
            return
        self._drift_next = now + 10.0
        fe = self.b.frontend
        if not fe.start_wall:
            return
        drift = fe.stream_now() - (now - fe.start_wall)
        if abs(drift - self._drift_logged) > 0.5:
            self.log(f"CLOCK stream-vs-wall drift {drift:+.2f}s "
                     f"(was {self._drift_logged:+.2f}s) — capture anomaly; "
                     f"gate/PTT/D5 use the stream clock and are unaffected")
            self._drift_logged = drift

    # -- event-driven disconnect monitor --------------------------------
    # 5 s polling missed a case-close-and-reopen entirely (bench 2026-08-26):
    # the buds auto-reconnected between polls and writes never stalled, so no
    # rung fired and stale audio played out serially. bluetoothctl's event
    # stream catches ANY disconnect, however brief.
    def _start_bt_monitor(self):
        def watch():
            while not self._stopping.is_set():
                try:
                    p = subprocess.Popen(["bluetoothctl"],
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, text=True)
                    for line in p.stdout:
                        if self._stopping.is_set():
                            break
                        if "Connected: no" in line and _MAC.replace(":", ":") \
                                and _MAC in line.replace("_", ":"):
                            self._disconnect_seen = True
                        elif "Connected: no" in line:
                            # property lines don't always carry the MAC;
                            # one earbud device is all we manage
                            self._disconnect_seen = True
                    p.terminate()
                except Exception as e:
                    self.log(f"bt monitor error: {e}; restarting watcher")
                    time.sleep(3)
        self._disconnect_seen = False
        t = threading.Thread(target=watch, name="bt-monitor", daemon=True)
        t.start()

    def _wait_for_controller(self):
        t0 = time.time()
        while not self._stopping.is_set():
            rc, out = _run(["bluetoothctl", "list"], timeout=5)
            if rc == 0 and "Controller" in out:
                if time.time() - t0 > 3:
                    self.log(f"controller present after "
                             f"{time.time()-t0:.0f}s wait")
                return
            if time.time() - t0 > 5:
                self.b.set_fault("Bluetooth starting…")
            time.sleep(1.0)

    # -- capture ---------------------------------------------------------
    def _check_capture(self, now):
        fe = self.b.frontend
        if fe.capture_alive() or now < self._cap_next_try:
            if fe.capture_alive() and self._cap_failures and \
                    now - fe.cap_started_wall > 5:
                self.log("capture stable again")
                self._cap_failures = 0
                self.b.set_fault("")
            return
        uptime = (fe.cap_started_wall and
                  now - fe.cap_started_wall) or 0
        instant = uptime < 3.0
        self._cap_failures += 1
        if instant:
            backoff = min(30, 5 * self._cap_failures)
            self.log(f"capture died INSTANTLY on open (attempt "
                     f"{self._cap_failures}) — receiver unplugged or node "
                     f"wrong; retry in {backoff}s")
            self.b.set_fault("Microphone lost — check the DJI receiver")
        else:
            backoff = min(10, self._cap_failures)
            self.log(f"capture died after {uptime:.0f}s running — "
                     f"reopening (attempt {self._cap_failures})")
            self.b.set_fault("Reconnecting microphone…")
        self._cap_next_try = now + backoff
        try:
            fe.restart_capture()
        except Exception as e:
            self.log(f"capture reopen failed: {e}")

    # -- bluetooth / playback -------------------------------------------
    def _bt_connected(self):
        rc, out = _run(["bluetoothctl", "info", _MAC], timeout=8)
        return rc == 0 and "Connected: yes" in out

    def _playback_sick(self, now):
        pb = self.b.playback
        if pb is None:
            return False
        if pb.error:
            return True
        lw = pb.last_write_wall
        return lw is not None and now - lw > 3.0   # stall: writes stopped

    def _check_bt(self, now):
        if now < self._bt_poll_next:
            return
        self._bt_poll_next = now + 5.0
        sick = self._playback_sick(now)
        connected = self._bt_connected()
        if connected and not sick:
            if getattr(self, "_disconnect_seen", False):
                # the buds dropped and came back on their own between polls —
                # rebuild the stream and run the stale-audio drop anyway
                self._disconnect_seen = False
                self.log("disconnect event observed (auto-reconnected) — "
                         "rebuilding stream, dropping stale audio")
                self.b.restart_playback()
            if self._bt_attempt:
                self.log("bluetooth healthy again")
                self._bt_attempt = 0
                self.b.set_fault("")
            return
        self._disconnect_seen = False  # full recovery path handles it below
        # The on-screen state is plain and persistent while unhealthy —
        # recovery detail stays in the log. (Toby, 2026-08-27: "not
        # connected" must be visible on the glass, not only logged.)
        if not connected:
            self.b.set_fault("Earbuds not connected — open the AirPods case")
        else:
            self.b.set_fault("Earbud audio stalled — reconnecting…")
        if now < self._bt_next_try:
            return
        self._bt_attempt += 1
        level = min(2, (self._bt_attempt - 1) // 2)
        self.log(f"bt recovery attempt {self._bt_attempt} (level {level}): "
                 f"connected={connected} playback_sick={sick}")
        ok = False
        if level == 0:
            rc, _ = _run(["bluetoothctl", "connect", _MAC], timeout=20)
            ok = rc == 0 and self._bt_connected()
        elif level == 1:
            _run(["bluetoothctl", "power", "off"], timeout=8)
            time.sleep(2)
            _run(["bluetoothctl", "power", "on"], timeout=8)
            time.sleep(2)
            _run(["bluetoothctl", "connect", _MAC], timeout=20)
            ok = self._bt_connected()
        else:
            # armed by /etc/sudoers.d/011_translator-bt: NOPASSWD for exactly
            # this one command (v1's pattern), nothing broader
            rc, out = _run(["sudo", "-n", "/usr/bin/systemctl",
                            "restart", "bluetooth"], timeout=20)
            if rc != 0:
                self.log("LEVEL-2 NOT ARMED: sudo -n denied — install "
                         "/etc/sudoers.d/011_translator-bt (see CLAUDE.md)")
                self.b.set_fault("Earbuds lost — restart Bluetooth manually")
            else:
                self._wait_for_controller()
                _run(["bluetoothctl", "connect", _MAC], timeout=20)
                ok = self._bt_connected()
        if ok:
            self.log(f"bt recovered at level {level} — rebuilding audio stream")
            self.b.restart_playback()
            self._bt_attempt = 0
            self.b.set_fault("")
        else:
            backoff = min(30, [3, 5, 10, 20][min(3, self._bt_attempt - 1)])
            self._bt_next_try = now + backoff
            self.log(f"attempt {self._bt_attempt} failed; next in {backoff}s")

    # -- thermal ---------------------------------------------------------
    def _check_thermal(self, now):
        if now < self._therm_next:
            return
        self._therm_next = now + 10.0
        rc, out = _run(["vcgencmd", "measure_temp"], timeout=5)
        if rc != 0:
            return
        try:
            t = float(out.split("=")[1].split("'")[0])
        except (IndexError, ValueError):
            return
        if t >= 80.0 and not self._therm_fault:
            self._therm_fault = True
            self.log(f"THERMAL {t:.1f}°C — soft cap; throttle costs +37% on "
                     f"every stage")
            self.b.set_fault(f"⚠ {t:.0f}°C — device is overheating")
        elif t < 78.0 and self._therm_fault:
            self._therm_fault = False
            self.log(f"thermal recovered ({t:.1f}°C)")
            self.b.set_fault("")
