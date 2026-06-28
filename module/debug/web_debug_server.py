from flask import Flask, jsonify
from module.debug.commission_debug import CommissionDebugHandler
import threading

app = Flask(__name__)

# 这里会被主程序注入
DEBUG_HANDLER = None


@app.route("/debug/gem")
def debug_gem():
    if DEBUG_HANDLER:
        DEBUG_HANDLER.trigger_gem_test()
    return jsonify({"status": "ok", "action": "gem_test"})


@app.route("/debug/cube")
def debug_cube():
    if DEBUG_HANDLER:
        DEBUG_HANDLER.trigger_cube_test()
    return jsonify({"status": "ok", "action": "cube_test"})


@app.route("/debug/big")
def debug_big():
    if DEBUG_HANDLER:
        DEBUG_HANDLER.trigger_big_success()
    return jsonify({"status": "ok", "action": "big_success"})


@app.route("/debug/notify")
def debug_notify():
    if DEBUG_HANDLER:
        DEBUG_HANDLER.trigger_notify_only()
    return jsonify({"status": "ok", "action": "notify_test"})


def run_server(host="127.0.0.1", port=8765):
    app.run(host=host, port=port, debug=False, use_reloader=False)


def start_debug_server(handler):
    global DEBUG_HANDLER
    DEBUG_HANDLER = handler

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()