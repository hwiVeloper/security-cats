"""백그라운드 작업 (PLAN.md 6.1).

점검·연결 테스트는 별도 스레드에서 실행하고, 진행 이벤트는 큐에 넣는다.
UI는 after()로 큐를 꺼내 메인 스레드에서만 위젯을 고친다.
호스트 키 확인처럼 사용자에게 물어야 하는 일은 큐로 메인 스레드에 요청하고 답을 기다린다.
"""
import queue
import threading


class Worker:
    def __init__(self, root, ask_host_key):
        """ask_host_key(server, host, key_type, fingerprint) -> bool  (메인 스레드에서 호출됨)"""
        self.root = root
        self.ask_host_key = ask_host_key
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None
        self.handlers = None
        self._poll()

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, fn, on_event=None, on_done=None, on_error=None, **kwargs):
        """fn(on_event=..., cancel=..., on_unknown_host=..., **kwargs) 를 스레드에서 실행한다."""
        self.cancel = threading.Event()
        self.handlers = (on_event, on_done, on_error)

        def run():
            try:
                res = fn(on_event=lambda e: self.q.put(("event", e)), cancel=self.cancel,
                         on_unknown_host=self._ask_from_thread, **kwargs)
                self.q.put(("done", res))
            except BaseException as e:  # noqa: BLE001 - 화면에 오류를 알리기 위해 모두 받는다
                self.q.put(("error", e))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def _ask_from_thread(self, server, host, key_type, fp) -> bool:
        ev, box = threading.Event(), {}
        self.q.put(("hostkey", (server, host, key_type, fp, ev, box)))
        ev.wait()
        return box.get("ok", False)

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                on_event, on_done, on_error = self.handlers or (None, None, None)
                if kind == "event" and on_event:
                    on_event(data)
                elif kind == "done" and on_done:
                    on_done(data)
                elif kind == "error" and on_error:
                    on_error(data)
                elif kind == "hostkey":
                    server, host, key_type, fp, ev, box = data
                    try:
                        box["ok"] = bool(self.ask_host_key(server, host, key_type, fp))
                    finally:
                        ev.set()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)
