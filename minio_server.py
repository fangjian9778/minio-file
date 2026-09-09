# -*- coding: utf-8 -*-
"""
MinIO File Upload/Download Web Service
Supports minio SDK v2.x and v7.x, Python 2.7+
"""

from __future__ import print_function
import os
import sys
import json
import uuid
import time
import re
import fnmatch
import zipfile
import threading
from datetime import datetime
from io import BytesIO

from flask import Flask, request, jsonify, render_template, send_file, Blueprint, Response, make_response, redirect
from flask_cors import CORS
from minio import Minio
# 兼容不同版本 minio SDK 的错误类导入
try:
    from minio.error import S3Error
except ImportError:
    try:
        from minio import S3Error
    except ImportError:
        S3Error = Exception
from werkzeug.utils import secure_filename

# 检测 minio SDK 可用 API
try:
    MINIO_VERSION = tuple(int(x) for x in __import__("minio").__version__.split(".")[:2])
except Exception:
    MINIO_VERSION = (0, 0)

IS_MINIO_V7_API = hasattr(Minio, "list_buckets")

# Python 2.7 兼容: inspect.signature 不存在
try:
    from inspect import signature
    _MINIO_HAS_PORT = 'port' in signature(Minio).parameters
except Exception:
    _MINIO_HAS_PORT = False

print("MinIO SDK version string:", repr(__import__("minio").__version__))
print("Using", "v7-style API" if IS_MINIO_V7_API else "v2-style API")
print("Minio() accepts 'port' param:", _MINIO_HAS_PORT)


def _s3_error_message(e):
    """兼容获取 S3Error 的错误消息"""
    try:
        return str(getattr(e, "message", str(e)))
    except Exception:
        return str(e)


app = Flask(__name__)
CORS(app)
bp = Blueprint("main", __name__, url_prefix="/minio")


# =================== Config Storage ===================
minio_config = {
    "endpoint": "",
    "access_key": "",
    "secret_key": "",
    "secure": False,
    "host": "",
    "port": "9000",
}

# Per-bucket path configuration
path_config = {}

minio_client = None
upload_progress = {}
upload_progress_lock = threading.Lock()

def _resolve_config_file():
    """返回配置文件路径: 优先 exe 所在目录(便携模式), 目录不可写时回退到 %APPDATA%(安装模式)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(base_dir, ".minio_config.json")
    if os.access(base_dir, os.W_OK):
        return local_path
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    fallback_dir = os.path.join(appdata, "MinIOFileService")
    try:
        if not os.path.exists(fallback_dir):
            os.makedirs(fallback_dir)
        return os.path.join(fallback_dir, ".minio_config.json")
    except Exception:
        return local_path


CONFIG_FILE = _resolve_config_file()


# =================== MinIO Helpers ===================

def _create_minio_client(host, port, access_key, secret_key, secure):
    """Create MinIO client, auto-detect port param support."""
    port_int = int(port) if str(port).isdigit() else 9000
    if _MINIO_HAS_PORT:
        return Minio(host, port=port_int, access_key=access_key,
                     secret_key=secret_key, secure=secure)
    else:
        return Minio(host + ":" + str(port), access_key=access_key,
                     secret_key=secret_key, secure=secure)


def get_minio_client():
    global minio_client
    if not minio_config.get("access_key"):
        return None
    if minio_client is None:
        try:
            host = minio_config.get("host", "")
            port = minio_config.get("port", "9000")
            minio_client = _create_minio_client(
                host, port,
                minio_config["access_key"],
                minio_config["secret_key"],
                minio_config["secure"],
            )
        except Exception:
            minio_client = None
            raise
    return minio_client


# =================== Persistence ===================

def save_config(config):
    global minio_config, minio_client
    minio_config.update(config)
    minio_client = None
    _persist_config()


def _persist_config():
    data = {
        "minio_config": minio_config,
        "path_config": path_config,
    }
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_config():
    global minio_config, path_config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
                if "minio_config" in saved:
                    minio_config.update(saved["minio_config"])
                if "path_config" in saved:
                    path_config.update(saved["path_config"])
                # 兼容老配置: 丢弃 local_config / local_path_config 等已下线模块的键
                for _k in ("local_config", "local_path_config"):
                    saved.pop(_k, None)
        except Exception:
            pass


# =================== Endpoint Parsing ===================

def _parse_endpoint(endpoint):
    host = endpoint
    port = "9000"
    if endpoint:
        host = endpoint.strip("[]")
        if "[" in endpoint:
            parts = endpoint.split("]:")
            if len(parts) == 2:
                host = parts[0].strip("[]")
                port = parts[1].strip()
        elif ":" in endpoint:
            last_colon = endpoint.rfind(":")
            last_part = endpoint[last_colon + 1:]
            if last_part.isdigit() and len(last_part) <= 5:
                host = endpoint[:last_colon]
                port = last_part
    return host, port


# =================== Route: Pages ===================

@bp.route("/")
def index():
    """主页: 直接渲染, 无需鉴权."""
    return render_template("index.html")


# =================== Route: MinIO Config ===================

@bp.route("/api/config", methods=["GET"])
def get_config():
    host, port = _parse_endpoint(minio_config.get("endpoint", ""))
    # Fallback to saved host/port
    if not host and minio_config.get("host"):
        host = minio_config["host"]
        port = minio_config.get("port", "9000")
    safe_config = {
        "host": host,
        "port": port,
        "endpoint": host + ":" + port,
        "access_key": minio_config["access_key"],
        "secure": minio_config["secure"],
        "connected": False,
    }
    try:
        client = get_minio_client()
        if client:
            client.list_buckets() if IS_MINIO_V7_API else client.bucket_list()
            safe_config["connected"] = True
    except Exception:
        pass
    return jsonify(safe_config)


@bp.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(force=True)
    host = data.get("host", "").strip()
    port = data.get("port", "9000").strip()
    raw_endpoint = data.get("endpoint", "").strip()

    host = host.strip("[]")
    if raw_endpoint:
        raw_endpoint = raw_endpoint.strip("[]")

    if raw_endpoint:
        host, port = _parse_endpoint(raw_endpoint)
    elif not host:
        return jsonify({"error": "MinIO server address is required."}), 400

    config = {
        "endpoint": host + ":" + port,
        "access_key": data.get("access_key", "").strip(),
        "secret_key": data.get("secret_key", "").strip(),
        "secure": data.get("secure", False),
        "host": host,
        "port": port,
    }
    if not config["access_key"] or not config["secret_key"]:
        return jsonify({"error": "Access key and secret key are required."}), 400

    save_config(config)
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "Failed to create MinIO client."}), 500
        client.list_buckets() if IS_MINIO_V7_API else client.bucket_list()
        return jsonify({"message": "Connection successful!"})
    except S3Error as e:
        return jsonify({"error": "MinIO S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": "Connection failed: " + str(e)}), 500


# =================== Route: Buckets ===================

@bp.route("/api/buckets", methods=["GET"])
def list_buckets():
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400
        buckets = client.list_buckets() if IS_MINIO_V7_API else client.bucket_list()
        return jsonify({
            "buckets": [{"name": b.name, "creation_date": str(b.creation_date)} for b in buckets]
        })
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/buckets", methods=["POST"])
def create_bucket():
    data = request.get_json(force=True)
    bucket_name = data.get("bucket_name", "").strip()
    if not bucket_name:
        return jsonify({"error": "Bucket name is required."}), 400
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400
        exists = client.bucket_exists(bucket_name)
        if exists:
            return jsonify({"error": "Bucket already exists."}), 400
        client.make_bucket(bucket_name)
        return jsonify({"message": "Bucket created."})
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: MinIO Folders ===================

@bp.route("/api/folders", methods=["GET"])
def list_folders():
    """List folder paths (prefixes) in a bucket."""
    bucket_name = request.args.get("bucket", "")
    prefix = request.args.get("prefix", "")
    if not bucket_name:
        return jsonify({"error": "Bucket name is required."}), 400
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400

        # 规范化 prefix: 确保以 / 结尾或为空 (表示桶根目录)
        if prefix:
            normalized_prefix = prefix.rstrip("/") + "/"
        else:
            normalized_prefix = ""

        folders = set()

        # 优先使用 v7 的 recursive=False (CommonPrefix), 直接拿到子目录
        used_fast_path = False
        if IS_MINIO_V7_API:
            try:
                objects = client.list_objects(
                    bucket_name, prefix=normalized_prefix, recursive=False
                )
                for obj in objects:
                    if getattr(obj, "is_dir", False):
                        # CommonPrefix: object_name 已包含完整前缀并以 / 结尾
                        folders.add(obj.object_name)
                used_fast_path = True
            except TypeError:
                # 旧版 v7 不支持 recursive 参数, 回退
                used_fast_path = False

        if not used_fast_path:
            # v2 或回退路径: 递归列出所有对象, 提取子目录
            objects = client.list_objects(
                bucket_name, prefix=normalized_prefix, recursive=True
            )
            for obj in objects:
                name = obj.object_name
                if not name.startswith(normalized_prefix):
                    continue
                rel = name[len(normalized_prefix):]
                if "/" in rel:
                    first = rel.split("/", 1)[0]
                    folders.add(normalized_prefix + first + "/")

        return jsonify({
            "folders": sorted(list(folders)),
            "bucket": bucket_name,
            "prefix": normalized_prefix,
        })
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: Files ===================

@bp.route("/api/files", methods=["GET"])
def list_files():
    bucket_name = request.args.get("bucket", "")
    prefix = request.args.get("prefix", "")
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400
        if not bucket_name:
            return jsonify({"error": "Bucket name is required."}), 400

        objects = client.list_objects(bucket_name, prefix=prefix or "", recursive=True)
        files = []
        for obj in objects:
            if IS_MINIO_V7_API:
                last_mod = obj.last_modified.strftime("%Y-%m-%d %H:%M:%S") if obj.last_modified else ""
                files.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": last_mod,
                    "etag": obj.etag,
                })
            else:
                try:
                    stat = client.stat_object(bucket_name, obj.object_name)
                    files.append({
                        "name": obj.object_name,
                        "size": stat.size,
                        "last_modified": str(stat.last_modified),
                        "etag": stat.etag,
                    })
                except Exception:
                    files.append({
                        "name": obj.object_name,
                        "size": 0,
                        "last_modified": "",
                        "etag": "",
                    })
        return jsonify({"files": files, "bucket": bucket_name})
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: Upload ===================

@bp.route("/api/upload", methods=["POST"])
def upload_file():
    bucket_name = request.form.get("bucket", "")
    custom_name = request.form.get("custom_name", "").strip()
    upload_id = request.form.get("upload_id", str(uuid.uuid4()))

    if not bucket_name:
        return jsonify({"error": "Bucket name is required."}), 400
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    object_name = custom_name if custom_name else secure_filename(file.filename)

    with upload_progress_lock:
        upload_progress[upload_id] = {"current": 0, "total": 0, "status": "starting"}

    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400

        file.stream.seek(0, 2)
        file_size = file.stream.tell()
        file.stream.seek(0)

        with upload_progress_lock:
            upload_progress[upload_id] = {"current": 0, "total": file_size, "status": "uploading"}

        def progress_callback(bytes_sent, total_size):
            with upload_progress_lock:
                upload_progress[upload_id] = {"current": bytes_sent, "total": total_size, "status": "uploading"}

        extra_kwargs = {"content_type": file.content_type or "application/octet-stream"}
        if IS_MINIO_V7_API:
            extra_kwargs["progress"] = progress_callback
        else:
            extra_kwargs["callback"] = progress_callback

        client.put_object(bucket_name, object_name, file.stream, file_size, **extra_kwargs)

        with upload_progress_lock:
            upload_progress[upload_id] = {"current": file_size, "total": file_size, "status": "complete"}

        return jsonify({
            "message": "File uploaded successfully.",
            "bucket": bucket_name,
            "object_name": object_name,
            "size": file_size,
        })
    except S3Error as e:
        with upload_progress_lock:
            upload_progress[upload_id] = {"current": 0, "total": 0, "status": "error", "error": _s3_error_message(e)}
        return jsonify({"error": "Upload failed: " + _s3_error_message(e)}), 500
    except Exception as e:
        with upload_progress_lock:
            upload_progress[upload_id] = {"current": 0, "total": 0, "status": "error", "error": str(e)}
        return jsonify({"error": "Upload failed: " + str(e)}), 500


@bp.route("/api/upload/progress", methods=["GET"])
def get_upload_progress():
    upload_id = request.args.get("upload_id", "")
    with upload_progress_lock:
        progress = upload_progress.get(upload_id, {"status": "not_found"})
    return jsonify(progress)


# =================== Route: Download ===================

@bp.route("/api/download", methods=["GET"])
def download_file():
    bucket_name = request.args.get("bucket", "")
    object_name = request.args.get("object_name", "")
    if not bucket_name or not object_name:
        return jsonify({"error": "Bucket and object name are required."}), 400

    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400

        response = client.get_object(bucket_name, object_name)
        data = response.read()
        response.close()
        response.release_conn()

        filename = os.path.basename(object_name)
        content_disposition = 'attachment; filename*=UTF-8\'\'"' + filename + '"'
        return Response(
            data,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": content_disposition, "Content-Length": str(len(data))},
        )
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: Delete ===================

@bp.route("/api/delete", methods=["POST"])
def delete_file():
    data = request.get_json(force=True)
    bucket_name = data.get("bucket", "")
    object_name = data.get("object_name", "")
    if not bucket_name or not object_name:
        return jsonify({"error": "Bucket and object name are required."}), 400
    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400
        client.remove_object(bucket_name, object_name)
        return jsonify({"message": "File deleted successfully."})
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: Search ===================

@bp.route("/api/search", methods=["GET"])
def search_files():
    bucket_name = request.args.get("bucket", "")
    pattern = request.args.get("pattern", "").strip()
    search_type = request.args.get("type", "fuzzy")
    if not bucket_name:
        return jsonify({"error": "Bucket name is required."}), 400
    if not pattern:
        return jsonify({"error": "Search pattern is required."}), 400

    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400

        def matches(name):
            basename = os.path.basename(name)
            if search_type == "regex":
                try:
                    return bool(re.search(pattern, basename, re.IGNORECASE))
                except re.error:
                    return False
            elif search_type == "wildcard":
                return fnmatch.fnmatch(basename.lower(), pattern.lower())
            else:
                return pattern.lower() in basename.lower()

        objects = client.list_objects(bucket_name, recursive=True)
        files = []
        for obj in objects:
            if matches(obj.object_name):
                if IS_MINIO_V7_API:
                    last_mod = obj.last_modified.strftime("%Y-%m-%d %H:%M:%S") if obj.last_modified else ""
                    files.append({"name": obj.object_name, "size": obj.size, "last_modified": last_mod, "etag": obj.etag})
                else:
                    try:
                        stat = client.stat_object(bucket_name, obj.object_name)
                        files.append({"name": obj.object_name, "size": stat.size, "last_modified": str(stat.last_modified), "etag": stat.etag})
                    except Exception:
                        pass

        return jsonify({"files": files, "bucket": bucket_name, "pattern": pattern, "type": search_type, "count": len(files)})
    except S3Error as e:
        return jsonify({"error": "S3 Error: " + _s3_error_message(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== Route: Batch Download ===================

@bp.route("/api/batch-download", methods=["GET"])
def batch_download():
    bucket_name = request.args.get("bucket", "")
    object_list = request.args.get("files", "")
    if not bucket_name or not object_list:
        return jsonify({"error": "Bucket and file list are required."}), 400

    object_names = [name.strip() for name in object_list.split(",") if name.strip()]
    if not object_names:
        return jsonify({"error": "No files specified."}), 400

    try:
        client = get_minio_client()
        if not client:
            return jsonify({"error": "MinIO not configured."}), 400

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for obj_name in object_names:
                response = client.get_object(bucket_name, obj_name)
                data = response.read()
                response.close()
                response.release_conn()
                zf.writestr(obj_name, data)

        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": 'attachment; filename="' + bucket_name + '_batch.zip"'},
        )
    except Exception as e:
        return jsonify({"error": "Batch download failed: " + str(e)}), 500


# =================== Route: Path Config ===================

@bp.route("/api/path-config", methods=["GET"])
def get_path_config():
    bucket = request.args.get("bucket", "")
    if bucket:
        return jsonify({"bucket": bucket, "path": path_config.get(bucket, "")})
    return jsonify({"paths": path_config})


@bp.route("/api/path-config", methods=["POST"])
def set_path_config():
    data = request.get_json(force=True)
    bucket_name = data.get("bucket", "").strip()
    upload_path = data.get("path", "").strip()
    if not bucket_name:
        return jsonify({"error": "Bucket name is required."}), 400
    path_config[bucket_name] = upload_path
    _persist_config()
    return jsonify({"message": "Path config saved.", "bucket": bucket_name, "path": upload_path})


# =================== Health ===================

@bp.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "python_version": sys.version,
        "timestamp": datetime.now().isoformat(),
    })


# =================== Init ===================

load_config()
app.register_blueprint(bp)


@app.route("/")
def root_redirect():
    return render_template("index.html")


# =================== Broken Pipe / Client Disconnect Handling ===================
# Python 2.7 BaseHTTPServer/Werkzeug 会在客户端提前断开连接时抛出
# [Errno 32] Broken pipe / [Errno 54] Connection reset. 这些属于正常行为,
# 不应当作异常输出, 这里通过 WSGI middleware + Flask errorhandler 静默处理.

try:
    # Python 3
    _BrokenPipeError = BrokenPipeError
except NameError:
    # Python 2.7 没有 BrokenPipeError, 用 IOError 替代
    _BrokenPipeError = IOError

try:
    from werkzeug.serving import WSGIRequestHandler
    _orig_log_message = WSGIRequestHandler.log_message

    def _silent_log_message(self, format, *args):
        try:
            msg = format % args
        except Exception:
            msg = format
        # 客户端断开产生的 EPIPE/ECONNRESET 静默处理
        if "Broken pipe" in msg or "Connection reset" in msg:
            return
        _orig_log_message(self, format, *args)

    WSGIRequestHandler.log_message = _silent_log_message
except Exception:
    pass


@app.errorhandler(IOError)
def _handle_io_error(e):
    if getattr(e, "errno", None) in (32, 54):  # EPIPE, ECONNRESET
        # 客户端断开, 不再尝试回写
        return ""
    return jsonify({"error": "IO error: " + str(e)}), 500


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "Request entity too large."}), 413


# 包装 wsgi_app, 捕获 socket 关闭导致的写入异常
_orig_wsgi_app = app.wsgi_app


class _SafeIterable(object):
    """包装响应迭代器, 写入阶段出现 EPIPE/ECONNRESET 时静默吞掉."""

    def __init__(self, iterable):
        self._iter = iter(iterable)

    def __iter__(self):
        return self

    def next(self):  # Python 2 兼容
        return self.__next__()

    def __next__(self):
        try:
            return next(self._iter)
        except (StopIteration, _BrokenPipeError, IOError) as e:
            if isinstance(e, _BrokenPipeError) or (isinstance(e, IOError) and getattr(e, "errno", None) in (32, 54)):
                raise StopIteration()
            raise

    def close(self):
        try:
            if hasattr(self._iter, "close"):
                self._iter.close()
        except Exception:
            pass


def _safe_wsgi_app(environ, start_response):
    try:
        result = _orig_wsgi_app(environ, start_response)
        return _SafeIterable(result)
    except (_BrokenPipeError, IOError) as e:
        errno = getattr(e, "errno", None)
        if errno in (32, 54) or isinstance(e, _BrokenPipeError):
            # 客户端断开, 静默返回空响应
            try:
                start_response("200 OK", [("Content-Length", "0")])
            except Exception:
                pass
            return [b""]
        raise


app.wsgi_app = _safe_wsgi_app


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    print("Starting MinIO File Service...")
    print("Server will be available at: http://" + host + ":" + str(port) + "/minio")
    print("Python version: " + sys.version)
    print("MinIO SDK version: " + str(MINIO_VERSION) + (" (v7 mode)" if IS_MINIO_V7_API else " (v2 mode)"))

    app.run(host=host, port=port, debug=debug, threaded=True)
