"""Desktop console entry point; Ctrl+C stops the local server cleanly."""

import multiprocessing
import signal
import sys

from nopoach.app.main import launch

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        launch()
    except KeyboardInterrupt:
        pass
