import threading
import time
import sys

from server import app


def run_server():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def main():
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    # Wait briefly for server to start
    time.sleep(1.0)

    url = "http://127.0.0.1:5000"
    try:
        import webview  # pywebview
        webview.create_window("Disk Usage Analyzer", url, width=1200, height=800)
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(url)
        try:
            while t.is_alive():
                t.join(1.0)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()


