# background api daemon, mirrors bg_indexer.py shell (pid file, signal handlers)
from src.api_server import run
from src.config import api_settings
from src.paths import app_dir
import os
import signal
import threading

PID_FILE = app_dir() / "bg_api.pid"


def main():
    # read-only api, works without any nntp config
    s = api_settings()

    tmp = PID_FILE.with_suffix(".pid.tmp")
    tmp.write_text(str(os.getpid()))
    os.replace(tmp, PID_FILE)

    server = run(s["host"], s["port"], s["key"], serve=False)

    # serve_forever blocks, shutdown has to come from another thread
    def handle_stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    print(f"newznab api on http://{s['host']}:{s['port']}", flush=True)

    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
