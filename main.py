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


def _resolve_port():
    """返回当前 Flask 服务端口."""
    return _current_port


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


# =================== pywebview JS API (程序内下载/保存) ===================
# 暴露给前端 JS 的接口, 在程序内弹出系统保存对话框并写入本地路径,
# 避免跳到电脑默认浏览器下载.

_api_window = None
_floating_window = None
_is_mini_mode = False
_current_port = 5000


def _current_window():
    import webview
    if _api_window is not None:
        return _api_window
    if webview.windows:
        return webview.windows[0]
    return _api_window


class _JsApi(object):
    """通过 window.pywebview.api 暴露给前端的方法(返回 dict)."""

    def download(self, bucket, object_name, download_id=""):
        """保存单个文件: 弹出 SAVE 对话框选择本地路径, 支持进度回调."""
        from webview import SAVE_DIALOG
        import minio_server
        filename = os.path.basename(object_name) or object_name or "download"
        w = _current_window()
        if w is None:
            return {"status": "error", "message": "webview 窗口不可用"}
        try:
            result = w.create_file_dialog(SAVE_DIALOG, save_filename=filename)
        except TypeError:
            result = w.create_file_dialog(SAVE_DIALOG, save_filename=filename)
        if not result:
            return {"status": "cancelled"}
        save_path = result if isinstance(result, (str, bytes)) else result[0]
        try:
            client = minio_server.get_minio_client()
            if not client:
                return {"status": "error", "message": "MinIO 未配置"}
            # 获取文件大小
            stat = client.stat_object(bucket, object_name)
            total_size = stat.size
            resp = client.get_object(bucket, object_name)
            try:
                downloaded = 0
                with open(save_path, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if download_id:
                            with minio_server.download_progress_lock:
                                minio_server.download_progress[download_id] = {
                                    "current": downloaded,
                                    "total": total_size,
                                    "status": "downloading",
                                }
            finally:
                resp.close()
                resp.release_conn()
                if download_id:
                    with minio_server.download_progress_lock:
                        minio_server.download_progress[download_id] = {
                            "current": total_size,
                            "total": total_size,
                            "status": "complete",
                        }
            return {"status": "ok", "path": save_path}
        except Exception as e:
            _log("download error: " + str(e))
            if download_id:
                import minio_server
                with minio_server.download_progress_lock:
                    minio_server.download_progress[download_id] = {
                        "current": 0, "total": 0, "status": "error", "error": str(e)
                    }
            return {"status": "error", "message": str(e)}

    def batch_download(self, bucket, object_names):
        """批量下载: 弹出目录选择框, 逐个写入本地."""
        from webview import FOLDER_DIALOG
        if not object_names:
            return {"status": "error", "message": "没有要下载的文件"}
        w = _current_window()
        if w is None:
            return {"status": "error", "message": "webview 窗口不可用"}
        try:
            result = w.create_file_dialog(FOLDER_DIALOG)
        except TypeError:
            result = w.create_file_dialog(FOLDER_DIALOG)
        if not result:
            return {"status": "cancelled"}
        folder = result if isinstance(result, (str, bytes)) else result[0]
        try:
            import minio_server
            client = minio_server.get_minio_client()
            if not client:
                return {"status": "error", "message": "MinIO 未配置"}
            saved = 0
            for obj in object_names:
                basename = os.path.basename(obj) or obj
                local_path = os.path.join(folder, basename)
                resp = client.get_object(bucket, obj)
                try:
                    with open(local_path, "wb") as fh:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            fh.write(chunk)
                finally:
                    resp.close()
                    resp.release_conn()
                saved += 1
            return {"status": "ok", "count": saved, "folder": folder}
        except Exception as e:
            _log("batch download error: " + str(e))
            return {"status": "error", "message": str(e)}

    def toggle_mini_mode(self):
        """切换小窗模式: 缩小主窗口并打开悬浮侧边窗."""
        global _is_mini_mode, _floating_window
        import webview
        _is_mini_mode = not _is_mini_mode
        w = _current_window()
        if w is None:
            return {"status": "error", "message": "窗口不可用"}
        if _is_mini_mode:
            # 主窗口缩到最小
            w.resize(320, 500, activate=False)
            return {"status": "ok", "mini": True}
        else:
            # 恢复正常大小
            w.resize(1200, 800, activate=False)
            if _floating_window:
                try:
                    _floating_window.hide()
                except Exception:
                    pass
                _floating_window = None
            return {"status": "ok", "mini": False}

    def create_floating_window(self):
        """打开悬浮侧边窗: 仅含上传 + 下载快捷入口."""
        global _floating_window
        import webview
        if _floating_window:
            try:
                _floating_window.show()
            except Exception:
                pass
            return {"status": "ok", "message": "已显示"}
        # 新建小窗口, 加载同一个页面#panel-upload 或 #panel-download
        _floating_window = webview.create_window(
            "快捷操作",
            "http://127.0.0.1:%d/minio#floating" % _resolve_port(),
            width=280, height=400,
            on_top=True,
        )
        return {"status": "ok"}

    def set_window_size(self, width, height):
        """自定义窗口大小."""
        w = _current_window()
        if w is None:
            return {"status": "error", "message": "窗口不可用"}
        try:
            w.resize(int(width), int(height), activate=False)
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


def main():
    global LOG_FILE, _api_window, _current_port

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

    _current_port = port
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
        # 暴露 JS API, 让前端在程序内下载/保存到本地路径
        js_api = _JsApi()
        # 窗口图标: 与安装包/exe 图标保持一致 (pywebview >= 4.0 支持 icon 参数)
        window_kwargs = dict(
            width=1200,
            height=800,
            min_size=(900, 600),
            js_api=js_api,
        )
        icon_path = _resource_path(os.path.join("assets", "app.ico"))
        if os.path.exists(icon_path):
            window_kwargs["icon"] = icon_path
        _api_window = webview.create_window("MinIO 文件服务", url, **window_kwargs)
        webview.start()
        _log("Window closed, exiting.")
        # 窗口关闭即退出进程
        os._exit(0)
    except TypeError:
        # 旧版 pywebview 不支持 icon 参数时重试
        try:
            import webview
            js_api = _JsApi()
            _api_window = webview.create_window("MinIO 文件服务", url, width=1200, height=800,
                                                min_size=(900, 600), js_api=js_api)
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
