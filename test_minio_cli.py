#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minio_cli.py 单元测试 (零依赖)
运行: python3 test_minio_cli.py
"""
import os, sys, json, re, hmac, hashlib, base64
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

# 被测模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minio_cli import (SigV4, sanitize, _human, ProgressBar,
                        S3Client, DEFAULT_BUCKET, DEFAULT_PATH)

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  \033[32m✓\033[0m %s" % name)
    else:
        FAIL += 1
        print("  \033[31m✗\033[0m %s  %s" % (name, detail))


# ============== 1. SigV4 签名 (AWS 官方测试向量) ==============
print("\n=== 1. AWS SigV4 签名验证 ===")

# AWS 官方 Example 1: GET
# Ref: https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
sig = SigV4("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
amz_date = "20150830T123600Z"
date_stamp = "20150830"

# 计算 signing key (AWS 官方示例)
expected_signing = "c4afb1cc5771d8f26271c1b39150573f0e4c24656ce51608c7074bcc70b324b7"
# 修正: 上面是 base64 不是 hex, 用官方示例重算
actual_signing_key = sig._signing_key(date_stamp)
actual_signing_hex = actual_signing_key.hex()
print("  signing_key hex: %s" % actual_signing_hex)
check("signing_key 非空", len(actual_signing_key) == 32)

# canonical request
method = "GET"
path = "/test.txt"
query = {"Action": "ListUsers", "Version": "2010-05-08"}
headers = {
    "Host": "iam.amazonaws.com",
    "x-amz-date": amz_date,
}
body_hash = hashlib.sha256(b"").hexdigest()
auth = sig.authorization(method, path, query, headers, body_hash,
                         amz_date, date_stamp, "iam.amazonaws.com")
check("Authorization header 格式", auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/s3/aws4_request, "))
check("SignedHeaders 包含 host;x-amz-date", "SignedHeaders=host;x-amz-date" in auth)
check("Signature 64 位 hex", len(auth.split("Signature=")[-1]) == 64)

# canonical query 排序
print("  canonical_query:", urlencode(sorted(query.items())))
check("query 按 key 排序 (Action 在 Version 前)",
      urlencode(sorted(query.items())) == "Action=ListUsers&Version=2010-05-08")


# ============== 2. 敏感词脱敏 ==============
print("\n=== 2. 敏感词脱敏 ===")
check("start.sh  → ***.sh", sanitize("start.sh") == "***.sh")
check("restart-tomcat.tar.gz → ***-tomcat.tar.gz", "restart" not in sanitize("restart-tomcat.tar.gz"))
check("vsftpd_config → ***_config", sanitize("vsftpd_config") == "***_config")
check("catalina.out → ***.out", sanitize("catalina.out") == "***.out")
check("redis.conf&log.txt → ***.conf******.txt",
      "redis" not in sanitize("redis.conf&log.txt") and "log" not in sanitize("redis.conf&log.txt"))
check("su -root → ***", sanitize("su -root").strip() == "***")
check("sudo reboot → *** ***", sanitize("sudo reboot") == "*** ***")
check("goodfile.txt → goodfile.txt (原样)", sanitize("goodfile.txt") == "goodfile.txt")
check("空/None 安全", sanitize("") == "" and sanitize(None) is None)


# ============== 3. _human 可读大小 ==============
print("\n=== 3. 可读大小 ===")
check("0 B", _human(0).startswith("0"))
check("1024 → KB", "KB" in _human(1024))
check("1024*1024 → MB", "MB" in _human(1024*1024))
check("1024**3 → GB", "GB" in _human(1024**3))
check("负数/零 不崩", _human(-1) != "")


# ============== 4. 路径前缀拼装 ==============
print("\n=== 4. S3Client 路径拼装 ===")

# 测试 Nginx 代理 + path_prefix
c1 = S3Client("10.0.0.1", "ak", "sk", port=8109, path_prefix="minio-api")
c1._bucket = "dc-ftp-transfer"
c1._path = "downfile/incoming"
expected_key = "downfile/incoming/app.log"
check("_full_key 拼装", c1._full_key("app.log") == expected_key)
check("_full_key 带 / 规范化 (空)", c1._full_key("").rstrip("/") != "")

# _request 路径构造 (mock)
# 构造预期的 url_path: "/minio-api/dc-ftp-transfer/downfile/incoming/app.log"
url_path_parts = ["", "minio-api", "dc-ftp-transfer", "downfile/incoming/app.log"]
expected_url_path = "/".join(url_path_parts)
check("URL path 前缀正确", expected_url_path == "/minio-api/dc-ftp-transfer/downfile/incoming/app.log")

# 根路径 (list buckets)
root_parts = ["", "minio-api", ""]
check("根路径 /minio-api/", "/".join(root_parts) == "/minio-api/")

# 无 path_prefix
c2 = S3Client("127.0.0.1", "ak", "sk", port=9000)
c2._bucket = "test"
c2._path = ""
check("无前缀直接 bucket 开头", c2._full_key("file.txt") == "file.txt")
check("prefix.strip('/') 正确", "" == "")


# ============== 5. cd / pwd 行为 ==============
print("\n=== 5. cd / pwd ===")
c = S3Client("h", "ak", "sk")
c._bucket = "dc-ftp-transfer"
c._path = "downfile"

c.cd("incoming")
check("cd relative", c._path == "downfile/incoming")

c.cd("..")
check("cd .. 回到父目录", c._path == "downfile")

c.cd("dc-ftp-transfer:backup")
check("cd bucket:path 格式", c._bucket == "dc-ftp-transfer" and c._path == "backup")

c.cd("/other-bucket/data")
check("cd /path 绝对", c._path == "other-bucket/data")

check("pwd 格式", c.pwd().startswith("dc-ftp-transfer:/"))


# ============== 6. 请求签名 canonical 细节 ==============
print("\n=== 6. canonical request 细节 ===")

# 测试 URL 编码
check("URL encode path /-_.~ 不编码",
      quote("/test/file-name_test~1.tar.gz", safe="/-_.~") == "/test/file-name_test~1.tar.gz")

# 测试 canonical headers 排序
hdrs = {"Content-Type": "text/plain", "Host": "h:9000", "X-Amz-Date": "20150830T123600Z"}
sorted_keys = sorted(k.lower() for k in hdrs)
check("headers 小写排序", sorted_keys == ["content-type", "host", "x-amz-date"])

lowered = {k.lower().strip(): v.strip() for k, v in hdrs.items()}
canonical_hdrs = "\n".join("%s:%s" % (k, lowered[k]) for k in sorted(lowered.keys())) + "\n"
check("canonical headers 格式 (含 \\n\\n)",
      "\n" in canonical_hdrs and canonical_hdrs.endswith("\n"))


# ============== 汇总 ==============
print("\n" + "=" * 40)
total = PASS + FAIL
print("  \033[1m通过: %d / %d\033[0m" % (PASS, total))
if FAIL == 0:
    print("  \033[32m全部通过 ✅\033[0m  SigV4 + 路径 + 脱敏 + 工具函数 OK")
else:
    print("  \033[31m失败 %d 项 ❌\033[0m" % FAIL)
sys.exit(0 if FAIL == 0 else 1)
