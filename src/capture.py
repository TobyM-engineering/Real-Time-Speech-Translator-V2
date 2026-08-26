"""Capture transport (D2): a pw-record child streaming RAW s16 stereo to
stdout ("-" target — verified on this hardware 2026-08-26; the WAV-to-FIFO
route fails because libsndfile can't write WAV to a non-seekable pipe).
All timing downstream derives from the sample counter, never wall clock."""
import os
import subprocess
import threading

from src import config

_BLOCK_BYTES = config.CHUNK * 2 * 2  # 512 frames x 2 ch x int16


class CaptureThread(threading.Thread):
    """Reads raw blocks and calls on_block(bytes, first_sample_index).
    on_block runs on this thread — keep it cheap (VAD and energy math only;
    models never run here)."""

    def __init__(self, on_block, node=config.DJI_NODE):
        super().__init__(name="capture", daemon=True)
        self.on_block = on_block
        self.node = node
        self.error = None
        self._stop = threading.Event()
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
        self._proc = subprocess.Popen(
            ["pw-record", "--target", self.node, "--rate", str(config.SR),
             "--channels", "2", "--format", "s16", "-"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def run(self):
        try:
            out = self._proc.stdout
            buf = b""
            sample_index = 0
            while not self._stop.is_set():
                need = _BLOCK_BYTES - len(buf)
                if need > 0:
                    b_ = out.read(need)
                    if not b_:
                        raise RuntimeError("capture stream EOF (pw-record died "
                                           "or DJI receiver vanished)")
                    buf += b_
                    continue
                block, buf = buf[:_BLOCK_BYTES], buf[_BLOCK_BYTES:]
                self.on_block(block, sample_index)
                sample_index += config.CHUNK
        except Exception as e:  # surfaced by the main loop; T6 owns restarts later
            if not self._stop.is_set():
                self.error = e

    def stop(self):
        self._stop.set()
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            self._proc.kill()
