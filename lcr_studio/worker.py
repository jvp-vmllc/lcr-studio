"""Background thread that owns the meter: streams readings and runs queued commands."""
from __future__ import annotations

import queue
import threading
import time

import serial
from PySide6.QtCore import QThread, Signal

from .ut622e import MeterError, Reading, open_meter


class JobContext:
    """Handed to long-running jobs (e.g. sweeps) so they can report progress and be aborted."""

    def __init__(self, worker: "MeterWorker", tag: str):
        self._worker = worker
        self.tag = tag

    def progress(self, i: int, n: int, msg: str = ""):
        self._worker.progress.emit(self.tag, i, n, msg)

    def partial(self, data):
        self._worker.jobPartial.emit(self.tag, data)

    @property
    def aborted(self) -> bool:
        return self._worker._abort.is_set()


class MeterWorker(QThread):
    connected = Signal(str)             # *IDN? reply
    disconnected = Signal(str)          # reason ("" for user request)
    reading = Signal(object)            # Reading
    settingsChanged = Signal(dict)
    jobDone = Signal(str, object)
    jobError = Signal(str, str)
    jobPartial = Signal(str, object)
    progress = Signal(str, int, int, str)
    traffic = Signal(str, str, bool)    # direction, text, quiet (polling)
    status = Signal(str)

    SYNC_INTERVAL = 3.0

    def __init__(self):
        super().__init__()
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._abort = threading.Event()
        self.meter = None
        self.state: dict = {}
        self.acquire = True
        self.sync_front_panel = True
        self._misses = 0

    # ------------------------------------------------ GUI-thread API --
    def open_port(self, port: str, baud: int):
        self._q.put(("open", (port, baud)))

    def close_port(self):
        self._q.put(("close", None))

    def call(self, fn, tag: str = "", refresh=True):
        """Run fn(meter) on the worker thread; result arrives via jobDone(tag, result).

        refresh: False, True (re-read core settings) or "full" (also comparator/correction state).
        """
        self._q.put(("call", (fn, tag, refresh)))

    def job(self, fn, tag: str):
        """Run fn(meter, ctx) as an abortable long job; settings are fully re-read afterwards."""
        self._abort.clear()
        self._q.put(("job", (fn, tag)))

    def trigger(self):
        self._q.put(("trigger", None))

    def abort_job(self):
        self._abort.set()

    def shutdown(self):
        self._stop.set()
        self._abort.set()
        self.wait(4000)

    # --------------------------------------------------- thread body --
    def run(self):
        last_sync = time.monotonic()
        while not self._stop.is_set():
            try:
                self._drain()
                if self.meter is None:
                    time.sleep(0.05)
                    continue
                if self.acquire and self.state.get("trigger") == "AUTO":
                    self._fetch()
                else:
                    time.sleep(0.05)
                if self.sync_front_panel and time.monotonic() - last_sync > self.SYNC_INTERVAL:
                    self._refresh(full=False)
                    last_sync = time.monotonic()
            except (serial.SerialException, OSError) as exc:
                self._drop(f"Connection lost: {exc}")
            except MeterError as exc:
                self.status.emit(str(exc))
        self._drop("")

    def _log(self, direction, text, quiet):
        self.traffic.emit(direction, text, quiet)

    def _drain(self):
        while True:
            try:
                op, arg = self._q.get_nowait()
            except queue.Empty:
                return
            if op == "open":
                self._open(*arg)
            elif op == "close":
                self._drop("")
            elif self.meter is None:
                if op in ("call", "job"):
                    self.jobError.emit(arg[1], "Meter not connected")
            elif op == "call":
                fn, tag, refresh = arg
                try:
                    result = fn(self.meter)
                    self.jobDone.emit(tag, result)
                except (MeterError, ValueError) as exc:
                    self.jobError.emit(tag, str(exc))
                if refresh:
                    self._refresh(full=(refresh == "full"))
                    self._discard_stale()
            elif op == "job":
                fn, tag = arg
                try:
                    result = fn(self.meter, JobContext(self, tag))
                    self.jobDone.emit(tag, result)
                except (MeterError, ValueError) as exc:
                    self.jobError.emit(tag, str(exc))
                self._refresh(full=True)
                self._discard_stale()
            elif op == "trigger":
                try:
                    self._emit_reading(*self.meter.trigger_fetch())
                except MeterError as exc:
                    self.status.emit(str(exc))

    def _open(self, port, baud):
        self._drop(None)
        try:
            meter = open_meter(port, baud, log=self._log)
        except (serial.SerialException, OSError) as exc:
            hint = ""
            if "Permission denied" in str(exc) or "Errno 13" in str(exc):
                hint = " — on Linux add yourself to the 'dialout' group (then log out/in) or install the .deb, which adds a udev rule."
            elif "busy" in str(exc).lower() or "Access is denied" in str(exc):
                hint = " — the port is in use; close the official UNI-T software."
            self.disconnected.emit(f"Could not open {port}: {exc}{hint}")
            return
        try:
            idn = meter.idn()
            try:
                meter.set_fetch_auto(False)   # replies must only come when we ask
            except MeterError:
                pass
            self.meter = meter
            self.state = {}
            self._refresh(full=True)
        except (MeterError, serial.SerialException, OSError) as exc:
            meter.close()
            self.meter = None
            self.disconnected.emit(f"No answer from meter on {port} ({exc}). Check the baud rate "
                                   "and that the official software is closed.")
            return
        self._misses = 0
        self.connected.emit(idn)

    def _drop(self, reason):
        if self.meter is not None:
            self.meter.close()
            self.meter = None
            self.state = {}
            if reason is not None:
                self.disconnected.emit(reason)

    def _refresh(self, full: bool):
        new = self.meter.read_settings(full=full)
        merged = dict(self.state, **{k: v for k, v in new.items() if v is not None or k == "secondary"})
        if merged != self.state:
            self.state = merged
            self.settingsChanged.emit(dict(merged))

    def _discard_stale(self):
        # The first reading after a change may belong to the old configuration.
        if self.acquire and self.state.get("trigger") == "AUTO":
            try:
                self.meter.fetch()
            except MeterError:
                pass

    def _fetch(self):
        try:
            p, s, c = self.meter.fetch()
        except MeterError as exc:
            self._misses += 1
            if self._misses == 3:
                self.status.emit(f"Meter not responding ({exc})")
            return
        self._misses = 0
        self._emit_reading(p, s, c)

    def _emit_reading(self, p, s, c):
        st = self.state
        stype = st.get("secondary") if st.get("primary") != "DCR" else None
        self.reading.emit(Reading(
            primary=p, secondary=s if stype else None, compare=c,
            ptype=st.get("primary", "?"), stype=stype, frequency=st.get("frequency"),
            level=st.get("level"), equivalent=st.get("equivalent"), speed=st.get("speed"),
        ))
