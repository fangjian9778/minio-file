#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinIO File Transfer CLI — 零依赖版本 (仅用 Python 3 标准库)
适用于 BC Linux 8.2 (系统默认 python3 即可运行)
依赖: 无 — urllib / hashlib / hmac / base64 / xml.etree.ElementTree
用法:
  python3 minio_cli.py                        # 交互式
  python3 minio_cli.py ls -l                  # 单命令
  python3 minio_cli.py get "*.log" ./         # 模糊匹配下载
"""

from __future__ import print_function
import os
import sys
import json
import re
import hmac
import hashlib
import base64
import fnmatch
import cmd
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import (Request, urlopen, HTTPSHandler, HTTPHandler,
                            build_opener, install_opener)
from urllib.error import HTTPError, URLError

# ========= 敏感词过滤 =========
SENSITIVE_PATTERNS = [
    r'start', r'restart', r'stop', r'reboot',
    r'shutdown', r'init', r'run', r'kill',
    r'vsftpd', r'java', r'redis', r'catalina',
    r'&', r'log', r'su\s+-root', r'sudo',
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


def sanitize(text):
    if not text:
        return text
    return SENSITIVE_RE.sub("***", text)


# ========= 配置 =========
DEFAULT_BUCKET = "dc-ftp-transfer"
DEFAULT_PATH = "downfile"
DEFAULT_CONFIG = os.path.expanduser("~/.minio_cli.json")


def load_config(path=DEFAULT_CONFIG):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg, path=DEFAULT_CONFIG):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


# ========= 进度条 =========
class ProgressBar(object):
    def __init__(self, label, total):
        self.label = label
        self.total = total or 1
        self.current = 0
        self.width = 40
        self.started = False

    def update(self, length):
        self.current += length
        if not self.started:
            self.started = True
            sys.stdout.write("  %s [" % sanitize(self.label))
        pct = min(100, int(100 * self.current / self.total))
        filled = int(self.width * self.current / self.total)
        bar = "#" * filled + "-" * (self.width - filled)
        sys.stdout.write("\r  %s [%s] %3d%%  %s/%s" % (
            sanitize(self.label), bar, pct,
            _human(self.current), _human(self.total)))
        sys.stdout.flush()
        if self.current >= self.total:
            sys.stdout.write("\n")
            sys.stdout.flush()


def _human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f%s" % (n, u)
        n /= 1024.0
    return "%.1fPB" % n


# ========= AWS SigV4 签名 =========
class SigV4(object):
    ALGO = "AWS4-HMAC-SHA256"

    def __init__(self, access_key, secret_key, region="us-east-1", service="s3"):
        self.ak = access_key
        self.sk = secret_key
        self.region = region
        self.service = service

    @staticmethod
    def _sha256(data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _hmac(key, msg):
        if isinstance(key, str):
            key = key.encode("utf-8")
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).digest()

    def _signing_key(self, date_stamp):
        k_date = self._hmac(("AWS4" + self.sk).encode("utf-8"), date_stamp)
        k_region = hmac.new(k_date, self.region.encode("utf-8"), hashlib.sha256).digest()
        k_service = hmac.new(k_region, self.service.encode("utf-8"), hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
        return k_signing

    def authorization(self, method, path, query, headers, body_hash, amz_date, date_stamp, host):
        # canonical headers: 把所有 header 名称转小写再排序
        lowered = {k.lower().strip(): v.strip() for k, v in headers.items()}
        sorted_keys = sorted(lowered.keys())
        canonical_hdrs = "\n".join("%s:%s" % (k, lowered[k]) for k in sorted_keys) + "\n"
        signed_hdrs = ";".join(sorted_keys)
        canonical_query = urlencode(sorted(query.items()), doseq=True) if query else ""
        canonical = "\n".join([method, quote(path, safe="/-_.~"), canonical_query,
                              canonical_hdrs, signed_hdrs, body_hash])
        credential_scope = "%s/%s/%s/aws4_request" % (date_stamp, self.region, self.service)
        string_to_sign = "\n".join([self.ALGO, amz_date, credential_scope, self._sha256(canonical)])
        signing_key = self._signing_key(date_stamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        auth_header = "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
            self.ALGO, self.ak, credential_scope, signed_hdrs, signature)
        return auth_header


# ========= S3 REST 客户端 =========
class S3Client(object):
    def __init__(self, host, access_key, secret_key,
                 port=9000, secure=False, path_prefix=""):
        self.host = host
        self.port = int(port)
        self.ak = access_key
        self.sk = secret_key
        self.secure = bool(secure)
        self.path_prefix = path_prefix.strip("/")
        self.scheme = "https" if self.secure else "http"
        self.signer = SigV4(access_key, secret_key)
        self._bucket = DEFAULT_BUCKET
        self._path = DEFAULT_PATH

    @property
    def base_url(self):
        return "%s://%s:%d" % (self.scheme, self.host, self.port)

    @property
    def host_header(self):
        return "%s:%d" % (self.host, self.port)

    # ---- request ----
    def _request(self, method, bucket, key, query=None, data=None,
                 extra_headers=None, progress_cb=None, progress_total=None):
        # 构造 URL
        path_parts = [""]  # 开头斜杠
        if self.path_prefix:
            path_parts.append(self.path_prefix)
        path_parts.append(bucket)
        if key:
            path_parts.append(key.lstrip("/"))
        url_path = "/".join(path_parts)

        # 空 bucket = 根路径 (list buckets)
        if not bucket:
            url_path = "/" + (self.path_prefix + "/" if self.path_prefix else "")

        query = query or {}
        query_str = urlencode(sorted(query.items())) if query else ""
        url = self.base_url + url_path
        if query_str:
            url += "?" + query_str

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        # body hash
        if data is None:
            body = b""
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
        body_hash = hashlib.sha256(body).hexdigest()

        headers = {
            "Host": self.host_header,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": body_hash,
        }
        if extra_headers:
            headers.update(extra_headers)

        auth = self.signer.authorization(method, url_path, query, headers, body_hash,
                                         amz_date, date_stamp, self.host_header)
        headers["Authorization"] = auth

        req = Request(url, data=body if body else None, headers=headers, method=method)
        try:
            resp = urlopen(req)
        except HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            raise RuntimeError("HTTP %d  %s" % (e.code, body_err[:200]))
        except URLError as e:
            raise RuntimeError("网络错误: %s" % e.reason)

        # 流式读取 + 进度
        if progress_cb is not None and progress_total:
            chunks = []
            got = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                got += len(chunk)
                progress_cb(len(chunk))
            return b"".join(chunks), dict(resp.headers)
        else:
            return resp.read(), dict(resp.headers)

    # ---- connect ----
    def connect(self):
        try:
            # 列桶 (根路径 GET)
            self._request("GET", "", "")
            return True, "连接成功"
        except RuntimeError as e:
            return False, str(e)
        except Exception as e:
            return False, "连接失败: %s" % e

    # ---- list buckets (备用) ----
    def list_buckets(self):
        data, _ = self._request("GET", "", "")
        try:
            root = ET.fromstring(data)
        except Exception:
            return []
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        buckets = []
        for b in root.findall(".//%sBucket" % ns):
            name_el = b.find("%sName" % ns)
            if name_el is not None:
                buckets.append(name_el.text)
        return buckets

    # ---- list objects ----
    def list_objects(self, prefix="", recursive=False):
        """返回 [{name, size, last_mod, is_dir}, ...]"""
        results = []
        params = {"list-type": "2"}
        if prefix:
            params["prefix"] = prefix
        if not recursive:
            params["delimiter"] = "/"
        continuation = None
        ns = "http://s3.amazonaws.com/doc/2006-03-01/"

        while True:
            if continuation:
                params["continuation-token"] = continuation
            data, _ = self._request("GET", self._bucket, "", query=params)
            try:
                root = ET.fromstring(data)
            except Exception as e:
                raise RuntimeError("XML 解析失败: %s" % e)

            # 文件
            for c in root.findall(".//{%s}Contents" % ns):
                key_el = c.find("{%s}Key" % ns)
                size_el = c.find("{%s}Size" % ns)
                lm_el = c.find("{%s}LastModified" % ns)
                if key_el is None:
                    continue
                name = key_el.text
                size = int(size_el.text) if size_el is not None else 0
                lm_str = lm_el.text if lm_el is not None else ""
                results.append({
                    "name": name,
                    "size": size,
                    "last_mod_str": lm_str,
                    "is_dir": False,
                })

            # 目录 (CommonPrefixes)
            for cp in root.findall(".//{%s}CommonPrefixes" % ns):
                pfx_el = cp.find("{%s}Prefix" % ns)
                if pfx_el is None:
                    continue
                results.append({
                    "name": pfx_el.text,
                    "size": 0,
                    "last_mod_str": "",
                    "is_dir": True,
                })

            # 翻页
            is_trunc = root.findtext("{%s}IsTruncated" % ns) == "true"
            next_ct = root.findtext("{%s}NextContinuationToken" % ns)
            if not is_trunc or not next_ct:
                break
            continuation = next_ct
        return results

    # ---- head / stat ----
    def head(self, key):
        try:
            _, hdr = self._request("HEAD", self._bucket, key)
            return True, int(hdr.get("Content-Length", 0))
        except Exception:
            return False, 0

    # ---- put (upload) ----
    def put(self, local_path, remote_name=None):
        if not os.path.isfile(local_path):
            raise IOError("本地文件不存在: %s" % local_path)
        if remote_name is None:
            remote_name = os.path.basename(local_path)
        key = self._full_key(remote_name)
        size = os.path.getsize(local_path)
        bar = ProgressBar(remote_name, size)

        # 流式 PUT: 分块读
        with open(local_path, "rb") as f:
            body = f.read()
        self._request("PUT", self._bucket, key,
                      data=body, extra_headers={"Content-Length": str(size)})
        bar.current = size
        bar.update(0)
        return key

    # ---- get (download) ----
    def get(self, remote_name, local_dir="."):
        key = self._full_key(remote_name.lstrip("/"))
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, os.path.basename(remote_name.rstrip("/")))

        ok, size = self.head(key)
        if not ok:
            raise IOError("远程文件不存在: %s" % key)
        bar = ProgressBar(os.path.basename(remote_name.rstrip("/")), size)
        data, _ = self._request("GET", self._bucket, key, progress_cb=bar.update, progress_total=size)
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path

    # ---- helpers ----
    def _full_key(self, name):
        parts = []
        if self._path:
            parts.append(self._path)
        parts.append(name)
        return "/".join(parts).lstrip("/")

    # ---- cd / pwd ----
    def cd(self, target):
        target = target.strip()
        if ":" in target:
            b, _, p = target.partition(":")
            self._bucket = b.strip("/")
            self._path = p.strip("/")
            return True
        if target == "..":
            if self._path:
                parts = self._path.split("/")
                parts.pop()
                self._path = "/".join(parts)
            return True
        if target in (".", ""):
            return True
        if target.startswith("/"):
            self._path = target.lstrip("/")
            return True
        self._path = (self._path + "/" if self._path else "") + target.lstrip("/")
        return True

    def pwd(self):
        return "%s:/%s" % (self._bucket, self._path)

    # ---- list (对外) ----
    def ls(self, path="", show_long=False, recursive=False):
        if path:
            self.cd(path)
        prefix = (self._path + "/") if self._path else ""
        items = self.list_objects(prefix=prefix, recursive=recursive)

        results = []
        for it in items:
            # 去掉 prefix 前缀
            rel = it["name"]
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            if not rel:
                continue
            if it["is_dir"]:
                name = rel.rstrip("/") + "/"
            else:
                name = rel
            results.append({
                "name": name,
                "size": it["size"],
                "last_mod_str": it["last_mod_str"],
                "is_dir": it["is_dir"],
            })
        results.sort(key=lambda r: (not r["is_dir"], r["name"]))
        return results

    # ---- find by pattern ----
    def find(self, pattern):
        pattern = pattern.strip()
        prefix = (self._path + "/") if self._path else ""
        all_items = self.list_objects(prefix=prefix, recursive=False)
        matched = []
        for it in all_items:
            if it["is_dir"]:
                continue
            rel = it["name"]
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            if fnmatch.fnmatch(os.path.basename(rel), pattern) or \
               fnmatch.fnmatch(rel, pattern):
                matched.append(rel)
        return matched


# ========= 交互式 Shell =========
class MinIOShell(cmd.Cmd):
    def __init__(self, client):
        cmd.Cmd.__init__(self)
        self.c = client
        self._refresh_prompt()

    def _refresh_prompt(self):
        self.prompt = "(%s) " % sanitize(self.c.pwd())

    def _precmd(self, line):
        return line

    # ---- ls ----
    def do_ls(self, arg):
        """列出远程目录: ls [-l] [-R] [path]"""
        args = arg.split()
        show_long = "-l" in args
        recursive = "-R" in args
        path = ""
        for a in args:
            if a not in ("-l", "-R"):
                path = a
        try:
            items = self.c.ls(path, show_long=show_long, recursive=recursive)
        except Exception as e:
            print("\033[31mls 失败: %s\033[0m" % e)
            return
        if not items:
            print("(空)")
            return
        for it in items:
            name = sanitize(it["name"])
            if it["is_dir"]:
                print("\033[34m%-44s\033[0m  <DIR>" % name)
            else:
                size = _human(it["size"])
                lm = it["last_mod_str"] or ""
                print("%-44s  %-12s  %s" % (name, size, lm))
        print("\n共 %d 项" % len(items))

    def help_ls(self):
        print("""ls [-l] [-R] [path]
  列出远程目录. -l 长格式, -R 递归.""")

    def do_cd(self, arg):
        """切换目录"""
        if not arg:
            arg = DEFAULT_PATH
        if self.c.cd(arg):
            print(sanitize(self.c.pwd()))
            self._refresh_prompt()
        else:
            print("\033[31mcd 失败\033[0m")

    def do_pwd(self, arg):
        print(sanitize(self.c.pwd()))

    def do_lls(self, arg):
        """列出本地目录"""
        import subprocess
        cmd_args = ["ls", "-l"] + (arg.split() if arg else [])
        subprocess.call(cmd_args)

    def do_put(self, arg):
        """上传: put <file|pattern>"""
        import glob
        args = arg.split()
        if not args:
            print("\033[31m用法: put <本地文件或通配符>\033[0m")
            return
        pattern = args[0]
        local_dir = args[1] if len(args) > 1 else "."
        if any(c in pattern for c in "*?["):
            matched = sorted(glob.glob(os.path.join(local_dir, pattern)))
            if not matched:
                print("\033[33m本地无匹配 %s\033[0m" % sanitize(pattern))
                return
        else:
            matched = [pattern]
        ok = 0
        for f in matched:
            if not os.path.isfile(f):
                print("\033[33m跳过: %s\033[0m" % sanitize(f))
                continue
            try:
                remote = self.c.put(f)
                print("\033[32m[OK]\033[0m %s -> %s" % (
                    sanitize(os.path.basename(f)), sanitize(remote)))
                ok += 1
            except Exception as e:
                print("\033[31m[FAIL]\033[0m %s: %s" % (sanitize(f), e))
        print("\n上传完成: %d/%d" % (ok, len(matched)))

    def do_get(self, arg):
        """下载: get <pattern> [dir]"""
        args = arg.split()
        if not args:
            print("\033[31m用法: get <远程文件或通配符> [保存目录]\033[0m")
            return
        pattern = args[0]
        local_dir = args[1] if len(args) > 1 else "."
        if any(c in pattern for c in "*?["):
            try:
                matched = self.c.find(pattern)
            except Exception as e:
                print("\033[31m匹配失败: %s\033[0m" % e)
                return
            if not matched:
                print("\033[33m远程无匹配 %s\033[0m" % sanitize(pattern))
                return
            names = matched
        else:
            names = [pattern]
        ok = 0
        for name in names:
            try:
                saved = self.c.get(name, local_dir)
                print("\033[32m[OK]\033[0m %s -> %s" % (
                    sanitize(name), sanitize(saved)))
                ok += 1
            except Exception as e:
                print("\033[31m[FAIL]\033[0m %s: %s" % (sanitize(name), e))
        print("\n下载完成: %d/%d" % (ok, len(names)))

    def do_ls_buckets(self, arg):
        """列出所有桶"""
        try:
            buckets = self.c.list_buckets()
            for b in buckets:
                print(sanitize(b))
        except Exception as e:
            print("\033[31m失败: %s\033[0m" % e)

    def do_exit(self, arg):
        print("再见")
        return True
    def do_quit(self, arg):
        return self.do_exit(arg)
    def do_EOF(self, arg):
        print()
        return True

    def do_help(self, arg):
        print("""
可用命令:
  ls [-l] [-R] [path]   列出远程目录
  cd <path>             切换远程目录 (bucket:path 格式)
  pwd                   显示当前远程路径
  lls [path]            列出本地目录
  ls_buckets            列出所有桶
  put <file|pattern>    上传 (支持 * ? 通配符)
  get <pattern> [dir]   下载 (支持 * ? 通配符)
  exit / quit           退出
""")


# ========= 入口 =========
def main():
    parser = argparse.ArgumentParser(
        description="MinIO CLI (零依赖, BC Linux 8.2)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-H", "--host", help="MinIO 主机 (host:port)")
    parser.add_argument("-k", "--access-key", help="Access Key")
    parser.add_argument("-s", "--secret-key", help="Secret Key")
    parser.add_argument("--secure", action="store_true", help="HTTPS")
    parser.add_argument("--path-prefix", default="",
                        help="Nginx 代理前缀 (如 minio-api)")
    parser.add_argument("-b", "--bucket", default=DEFAULT_BUCKET,
                        help="默认桶 (%s)" % DEFAULT_BUCKET)
    parser.add_argument("-p", "--path", default=DEFAULT_PATH,
                        help="默认目录 (%s)" % DEFAULT_PATH)
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help="配置文件")
    parser.add_argument("--save-config", action="store_true", help="保存配置")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="一次性命令")
    args = parser.parse_args()

    cfg = load_config(args.config)
    host = args.host or cfg.get("host") or os.environ.get("MINIO_HOST", "")
    port = cfg.get("port", 9000)
    if host and ":" in host:
        host, _, port = host.partition(":")
    ak = args.access_key or cfg.get("access_key") or os.environ.get("MINIO_ACCESS_KEY", "")
    sk = args.secret_key or cfg.get("secret_key") or os.environ.get("MINIO_SECRET_KEY", "")
    secure = args.secure or cfg.get("secure", False)
    path_prefix = args.path_prefix or cfg.get("path_prefix", "")

    if not host or not ak or not sk:
        parser.error("缺少 host / access-key / secret-key (命令行 / 环境变量 / %s)" % args.config)

    if args.save_config:
        save_config({"host": host, "port": int(port),
                     "access_key": ak, "secret_key": sk,
                     "secure": secure, "path_prefix": path_prefix}, args.config)
        print("配置已保存到 %s" % args.config)

    client = S3Client(host, ak, sk, port=port, secure=secure, path_prefix=path_prefix)
    ok, msg = client.connect()
    if not ok:
        print("\033[31m%s\033[0m" % msg)
        sys.exit(1)
    client._bucket = args.bucket
    client._path = args.path.strip("/")

    print("\033[32m✓ %s\033[0m  当前: %s" % (sanitize(msg), sanitize(client.pwd())))

    if args.command:
        shell = MinIOShell.__new__(MinIOShell)
        shell.c = client
        shell._refresh_prompt = lambda: None
        try:
            shell.onecmd(" ".join(args.command))
        except SystemExit:
            pass
        return

    MinIOShell(client).cmdloop()


if __name__ == "__main__":
    main()
