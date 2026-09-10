# -*- coding: utf-8 -*-
"""
MinIO 文件服务 - 桌面版入口
双击 exe 后弹出程序窗口, Web 页面内置在窗口内, 无需手动打开浏览器输入网址.
依赖: pywebview (Windows 上使用 Edge WebView2 内核)
"""
import os
import socket
import sys
import threading
import time
import traceback

LOG_FILE = None


def _log(msg):
    """写入运行日志文件, 便于排查桌面版启动问题."""
    try:
        if LOG_FILE:
            with open(LOG_FILE, "a") as f:
                f.write(msg + "\n")
    except Exception:
        pass


def _find_free_port(start=5000):
    """从 start 开始寻找空闲端口."""
    for port in range(start, start + 200):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return 5050


def _wait_server_ready(port, timeout=15):
    """等待 Flask 服务就绪."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _resource_path(rel):
    """解析资源文件路径: 兼容 PyInstaller 打包(frozen) 与源码运行两种方式."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return os.path.join(base, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def _run_flask(host, port):
    """在后台线程中运行 Flask 服务."""
    try:
        import minio_server
        minio_server.app.run(host=host, port=port, debug=False,
                             threaded=True, use_reloader=False)
    except Exception:
        _log("Flask server error:\n" + traceback.format_exc())


def main():
    global LOG_FILE

    # 日志文件: 优先 exe 目录, 不可写时回退到 %APPDATA%
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if os.access(base_dir, os.W_OK):
        LOG_FILE = os.path.join(base_dir, "minio_file_service.log")
    else:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(appdata, "MinIOFileService")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            log_dir = base_dir
        LOG_FILE = os.path.join(log_dir, "minio_file_service.log")

    # 端口: 优先 PORT 环境变量, 否则自动找空闲端口
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "0") or 0)
    except ValueError:
        port = 0
    if port == 0:
        port = _find_free_port()

    _log("Starting MinIO File Service (GUI) ... port=%d" % port)

    # 后台启动 Flask
    t = threading.Thread(target=_run_flask, args=(host, port), daemon=True)
    t.start()

    if not _wait_server_ready(port):
        _log("Server failed to start in time")
        return

    url = "http://127.0.0.1:%d/minio" % port
    _log("URL: " + url)

    try:
        # 主线程运行 GUI 事件循环, 内置浏览器加载页面
        import webview
        # 窗口图标: 与安装包/exe 图标保持一致 (pywebview >= 4.0 支持 icon 参数)
        window_kwargs = dict(
            width=1200,
            height=800,
            min_size=(900, 600),
        )
        icon_path = _resource_path(os.path.join("assets", "app.ico"))
        if os.path.exists(icon_path):
            window_kwargs["icon"] = icon_path
        webview.create_window("MinIO 文件服务", url, **window_kwargs)
        webview.start()
        _log("Window closed, exiting.")
        # 窗口关闭即退出进程
        os._exit(0)
    except TypeError:
        # 旧版 pywebview 不支持 icon 参数时重试
        try:
            import webview
            webview.create_window("MinIO 文件服务", url, width=1200, height=800,
                                  min_size=(900, 600))
            webview.start()
            os._exit(0)
        except Exception as e:
            _log("webview unavailable (%s), fallback to system browser" % e)
            import webbrowser
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
    except Exception as e:
        # WebView2 不可用(如 Win7 未装运行库): 回退为打开系统浏览器
        _log("webview unavailable (%s), fallback to system browser" % e)
        import webbrowser
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
