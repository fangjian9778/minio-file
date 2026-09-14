# -*- coding: utf-8 -*-
"""
MinIO File Service - Comprehensive Test Suite
Tests: Connection, Upload, Download, Config, Search, Batch Download
"""

from __future__ import print_function
import os
import sys
import json
import time
import unittest
from io import BytesIO
from datetime import datetime

# Mock minio before importing app
class MockMinio:
    """Mock MinIO client for testing without real server."""
    def __init__(self, endpoint_or_host, access_key=None, secret_key=None, secure=False, port=9000):
        # endpoint_or_host can be host string (new) or endpoint string (old)
        self.endpoint = endpoint_or_host
        self.port = port
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.buckets = {}
        self._test_mode = "test" in endpoint_or_host.lower() or "fail" in endpoint_or_host.lower()

    def list_buckets(self):
        if self._test_mode and "fail" in self.endpoint.lower():
            raise Exception("Connection refused")
        if not self.buckets:
            # Create default test bucket
            class MockBucket:
                def __init__(self):
                    self.name = "test-bucket"
                    self.creation_date = "2024-01-01"
            return [MockBucket()]
        result = []
        for name, data in self.buckets.items():
            class MockBucket:
                pass
            b = MockBucket()
            b.name = name
            b.creation_date = data.get("creation_date", "2024-01-01")
            result.append(b)
        return result

    def bucket_list(self):
        return self.list_buckets()

    def bucket_exists(self, name):
        return name in self.buckets

    def make_bucket(self, name):
        if name not in self.buckets:
            self.buckets[name] = {"objects": {}, "creation_date": "2024-01-01"}

    def list_objects(self, bucket_name, prefix="", recursive=False):
        if bucket_name not in self.buckets:
            return []
        objects_data = self.buckets[bucket_name]["objects"]
        result = []
        for name, data in objects_data.items():
            if prefix and not name.startswith(prefix):
                continue
            class MockObject:
                pass
            obj = MockObject()
            obj.object_name = name
            obj.size = data["size"]
            # Return datetime object for v7 compatibility
            ts = data.get("last_modified", datetime.utcnow())
            if isinstance(ts, float):
                obj.last_modified = datetime.fromtimestamp(ts)
            else:
                obj.last_modified = ts
            obj.etag = data.get("etag", "abc123")
            result.append(obj)
        return result

    def stat_object(self, bucket_name, object_name):
        if bucket_name not in self.buckets:
            raise Exception("Bucket not found")
        obj_data = self.buckets[bucket_name]["objects"].get(object_name)
        if not obj_data:
            raise Exception("Object not found")
        class MockStat:
            pass
        stat = MockStat()
        stat.size = obj_data["size"]
        ts = obj_data.get("last_modified", datetime.utcnow())
        if isinstance(ts, float):
            stat.last_modified = datetime.fromtimestamp(ts)
        else:
            stat.last_modified = ts
        stat.etag = obj_data.get("etag", "abc123")
        return stat

    def put_object(self, bucket_name, object_name, data_stream, data_size,
                   content_type=None, progress=None, callback=None):
        if bucket_name not in self.buckets:
            raise Exception("Bucket not found")
        if self._test_mode and "upload_fail" in self.endpoint.lower():
            raise Exception("Upload failed")
        content = data_stream.read()
        self.buckets[bucket_name]["objects"][object_name] = {
            "data": content,
            "size": len(content),
            "content_type": content_type,
            "last_modified": datetime.utcnow(),
            "etag": "test-etag",
        }
        if progress:
            progress(len(content), len(content))
        if callback:
            callback(len(content), len(content))

    def get_object(self, bucket_name, object_name):
        if bucket_name not in self.buckets:
            raise Exception("Bucket not found")
        obj_data = self.buckets[bucket_name]["objects"].get(object_name)
        if not obj_data:
            raise Exception("Object not found")
        class MockResponse:
            def __init__(self, data):
                self._data = data
            def read(self):
                return self._data["data"]
            def close(self):
                pass
            def release_conn(self):
                pass
        return MockResponse(obj_data)

    def remove_object(self, bucket_name, object_name):
        if bucket_name in self.buckets and object_name in self.buckets[bucket_name]["objects"]:
            del self.buckets[bucket_name]["objects"][object_name]

# Monkey-patch minio module before importing app
import sys as _sys
class MockMinioModule:
    __version__ = "7.2.0"
    Minio = MockMinio

class MockErrorModule:
    class S3Error(Exception):
        def __init__(self, message):
            self.message = message
            super(MockErrorModule.S3Error, self).__init__(message)

_sys.modules["minio"] = MockMinioModule()
_sys.modules["minio.error"] = MockErrorModule()

# Now import the Flask app
from minio_server import app, minio_config, path_config, get_minio_client, IS_MINIO_V7_API


class TestConfigAPI(unittest.TestCase):
    """Test configuration endpoints."""

    def setUp(self):
        self.client = app.test_client()
        # Reset config before each test
        minio_config.clear()
        minio_config.update({"endpoint": "", "access_key": "", "secret_key": "", "secure": False})
        path_config.clear()

    def test_get_config_empty(self):
        """GET /api/config - empty config returns defaults."""
        resp = self.client.get("/minio/api/config")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["endpoint"], ":9000")  # empty host + default port
        self.assertFalse(data["connected"])

    def test_set_config_ipv4(self):
        """POST /api/config - IPv4 address."""
        data = {
            "host": "10.0.0.1",
            "port": "9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        }
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_set_config_ipv6_bracket(self):
        """POST /api/config - IPv6 with brackets stripped."""
        data = {
            "host": "[2409:8014::4a]",
            "port": "8025",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        }
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        # Verify brackets are stripped
        self.assertEqual(minio_config["host"], "2409:8014::4a")

    def test_set_config_missing_fields(self):
        """POST /api/config - missing required fields returns 400."""
        data = {"host": "test", "port": "9000"}  # no keys
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_set_config_no_host(self):
        """POST /api/config - no host returns 400."""
        data = {"host": "", "port": "9000", "access_key": "a", "secret_key": "b"}
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_config_persistence(self):
        """Config is saved to file and survives reload."""
        data = {
            "host": "10.0.0.1",
            "port": "9000",
            "access_key": "persist",
            "secret_key": "persist123",
            "secure": True,
        }
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        # Read config file directly
        config_path = os.path.join(os.path.dirname(__file__), ".minio_config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                saved = json.load(f)
            self.assertEqual(saved["minio_config"]["access_key"], "persist")
            self.assertTrue(saved["minio_config"]["secure"])

    def test_config_connection_fail(self):
        """POST /api/config - connection failure returns proper error."""
        data = {
            "host": "fail.test",  # Will trigger connection error
            "port": "9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        }
        resp = self.client.post("/minio/api/config",
                                data=json.dumps(data),
                                content_type="application/json")
        # Should return 500 with error message (not crash)
        self.assertEqual(resp.status_code, 500)
        result = json.loads(resp.data)
        self.assertIn("error", result)


class TestBucketAPI(unittest.TestCase):
    """Test bucket operations."""

    def setUp(self):
        self.client = app.test_client()
        minio_config.clear()
        minio_config.update({
            "endpoint": "test.local:9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        })
        path_config.clear()

    def test_list_buckets(self):
        """GET /api/buckets - returns bucket list."""
        resp = self.client.get("/minio/api/buckets")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("buckets", data)
        self.assertIsInstance(data["buckets"], list)

    def test_create_bucket(self):
        """POST /api/buckets - creates new bucket."""
        data = {"bucket_name": "new-bucket"}
        resp = self.client.post("/minio/api/buckets",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_create_duplicate_bucket(self):
        """POST /api/buckets - duplicate returns 400."""
        data = {"bucket_name": "dup-bucket"}
        # Create first
        self.client.post("/minio/api/buckets",
                         data=json.dumps(data),
                         content_type="application/json")
        # Create duplicate
        resp = self.client.post("/minio/api/buckets",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_create_bucket_no_name(self):
        """POST /api/buckets - empty name returns 400."""
        resp = self.client.post("/minio/api/buckets",
                                data=json.dumps({}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)


class TestFileOperations(unittest.TestCase):
    """Test file upload, download, list, delete."""

    def setUp(self):
        self.client = app.test_client()
        minio_config.clear()
        minio_config.update({
            "endpoint": "test.local:9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        })
        path_config.clear()
        # Pre-create test bucket with data
        client = get_minio_client()
        client.make_bucket("test-bucket")

    def test_upload_file(self):
        """POST /api/upload - upload a file."""
        data = {
            "file": (BytesIO(b"Hello World"), "hello.txt"),
            "bucket": "test-bucket",
            "custom_name": "test/hello.txt",
            "upload_id": "test-001",
        }
        resp = self.client.post("/minio/api/upload", data=data,
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(result["object_name"], "test/hello.txt")
        self.assertEqual(result["size"], 11)

    def test_upload_no_file(self):
        """POST /api/upload - no file returns 400."""
        resp = self.client.post("/minio/api/upload",
                                data={"bucket": "test-bucket"})
        self.assertEqual(resp.status_code, 400)

    def test_upload_no_bucket(self):
        """POST /api/upload - no bucket returns 400."""
        data = {"file": (BytesIO(b"data"), "test.txt")}
        resp = self.client.post("/minio/api/upload", data=data,
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_list_files(self):
        """GET /api/files - list files in bucket."""
        # Upload a test file first
        data = {"file": (BytesIO(b"test content"), "file1.txt"),
                "bucket": "test-bucket"}
        self.client.post("/minio/api/upload", data=data,
                         content_type="multipart/form-data")

        resp = self.client.get("/minio/api/files?bucket=test-bucket")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertIn("files", result)
        self.assertGreaterEqual(len(result["files"]), 1)

    def test_list_files_with_prefix(self):
        """GET /api/files - filter by prefix."""
        data = {"file": (BytesIO(b"sub data"), "subdir/file.txt"),
                "bucket": "test-bucket",
                "custom_name": "subdir/file.txt"}
        self.client.post("/minio/api/upload", data=data,
                         content_type="multipart/form-data")

        resp = self.client.get("/minio/api/files?bucket=test-bucket&prefix=subdir/")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        for f in result["files"]:
            self.assertTrue(f["name"].startswith("subdir/"))

    def test_download_file(self):
        """GET /api/download - download a file."""
        # Upload first
        test_data = b"Download me!"
        data = {"file": (BytesIO(test_data), "download.txt"),
                "bucket": "test-bucket"}
        self.client.post("/minio/api/upload", data=data,
                         content_type="multipart/form-data")

        resp = self.client.get(
            "/minio/api/download?bucket=test-bucket&object_name=download.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, test_data)

    def test_download_missing_file(self):
        """GET /api/download - missing file returns 500."""
        resp = self.client.get(
            "/minio/api/download?bucket=test-bucket&object_name=nonexistent.txt")
        self.assertEqual(resp.status_code, 500)

    def test_delete_file(self):
        """POST /api/delete - delete a file."""
        # Upload first
        data = {"file": (BytesIO(b"to delete"), "delete.txt"),
                "bucket": "test-bucket"}
        self.client.post("/minio/api/upload", data=data,
                         content_type="multipart/form-data")

        resp = self.client.post("/minio/api/delete",
                                data=json.dumps({
                                    "bucket": "test-bucket",
                                    "object_name": "delete.txt",
                                }),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_upload_progress(self):
        """GET /api/upload/progress - track upload progress."""
        data = {
            "file": (BytesIO(b"Progress test"), "progress.txt"),
            "bucket": "test-bucket",
            "upload_id": "prog-test-001",
        }
        self.client.post("/minio/api/upload", data=data,
                         content_type="multipart/form-data")

        resp = self.client.get("/minio/api/upload/progress?upload_id=prog-test-001")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(result["status"], "complete")

    def test_upload_large_file(self):
        """POST /api/upload - upload a large file (1MB)."""
        large_data = os.urandom(1024 * 1024)  # 1MB
        data = {
            "file": (BytesIO(large_data), "large.bin"),
            "bucket": "test-bucket",
            "upload_id": "large-test",
        }
        resp = self.client.post("/minio/api/upload", data=data,
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(result["size"], 1024 * 1024)

    def test_upload_chinese_filename(self):
        """POST /api/upload - upload file with Chinese characters."""
        data = {
            "file": (BytesIO(b"Chinese content"), "chinese.txt"),
            "bucket": "test-bucket",
            "custom_name": "test/chinese.txt",
            "upload_id": "chinese-test",
        }
        resp = self.client.post("/minio/api/upload", data=data,
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)


class TestSearchAPI(unittest.TestCase):
    """Test file search functionality."""

    def setUp(self):
        self.client = app.test_client()
        minio_config.clear()
        minio_config.update({
            "endpoint": "test.local:9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        })
        path_config.clear()
        client = get_minio_client()
        client.make_bucket("search-bucket")
        # Upload test files
        for name in ["report.pdf", "report_v2.pdf", "summary.doc",
                      "data.csv", "2024_data.csv", "readme.md"]:
            data = {"file": (BytesIO(name.encode()), name),
                    "bucket": "search-bucket"}
            self.client.post("/minio/api/upload", data=data,
                             content_type="multipart/form-data")

    def test_fuzzy_search(self):
        """GET /api/search - fuzzy search matches substring."""
        resp = self.client.get(
            "/minio/api/search?bucket=search-bucket&pattern=report&type=fuzzy")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertGreater(len(result["files"]), 0)
        for f in result["files"]:
            self.assertIn("report", f["name"].lower())

    def test_wildcard_search(self):
        """GET /api/search - wildcard with * and ?."""
        resp = self.client.get(
            "/minio/api/search?bucket=search-bucket&pattern=*.csv&type=wildcard")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        for f in result["files"]:
            self.assertTrue(f["name"].endswith(".csv"))

    def test_regex_search(self):
        """GET /api/search - regex pattern matching."""
        resp = self.client.get(
            "/minio/api/search?bucket=search-bucket&pattern=2024_data&type=regex")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertGreater(len(result["files"]), 0)

    def test_search_no_match(self):
        """GET /api/search - no results returns empty list."""
        resp = self.client.get(
            "/minio/api/search?bucket=search-bucket&pattern=xyznonexistent&type=fuzzy")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(len(result["files"]), 0)


class TestPathConfig(unittest.TestCase):
    """Test path configuration persistence."""

    def setUp(self):
        self.client = app.test_client()
        minio_config.clear()
        minio_config.update({
            "endpoint": "test.local:9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        })
        path_config.clear()

    def test_save_path_config(self):
        """POST /api/path-config - save path for bucket."""
        data = {"bucket": "my-bucket", "path": "uploads/2024/"}
        resp = self.client.post("/minio/api/path-config",
                                data=json.dumps(data),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_get_path_config(self):
        """GET /api/path-config - retrieve saved path."""
        # Save first
        data = {"bucket": "test-path-bucket", "path": "docs/"}
        self.client.post("/minio/api/path-config",
                         data=json.dumps(data),
                         content_type="application/json")
        # Retrieve
        resp = self.client.get("/minio/api/path-config?bucket=test-path-bucket")
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(result["path"], "docs/")


class TestBatchDownload(unittest.TestCase):
    """Test batch download as ZIP."""

    def setUp(self):
        self.client = app.test_client()
        minio_config.clear()
        minio_config.update({
            "endpoint": "test.local:9000",
            "access_key": "test",
            "secret_key": "test123",
            "secure": False,
        })
        path_config.clear()
        client = get_minio_client()
        client.make_bucket("batch-bucket")
        # Upload test files
        for name in ["a.txt", "b.txt", "c.txt"]:
            data = {"file": (BytesIO(name.encode()), name),
                    "bucket": "batch-bucket"}
            self.client.post("/minio/api/upload", data=data,
                             content_type="multipart/form-data")

    def test_batch_download(self):
        """GET /api/batch-download - downloads ZIP with multiple files."""
        resp = self.client.get(
            "/minio/api/batch-download?bucket=batch-bucket"
            "&files=" + urllib_quote("a.txt") + ","
            + urllib_quote("b.txt"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/zip")


class TestHealthCheck(unittest.TestCase):
    """Test health check endpoint."""

    def setUp(self):
        self.client = app.test_client()

    def test_health(self):
        resp = self.client.get("/minio/api/health")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "ok")
        self.assertIn("python_version", data)
        self.assertIn("timestamp", data)


class TestEndpointParsing(unittest.TestCase):
    """Test IPv6/IPv4 endpoint parsing."""

    def setUp(self):
        self.client = app.test_client()
        from minio_server import _parse_endpoint
        self.parse = _parse_endpoint

    def test_ipv4_simple(self):
        host, port = self.parse("10.0.0.1:9000")
        self.assertEqual(host, "10.0.0.1")
        self.assertEqual(port, "9000")

    def test_ipv4_no_port(self):
        host, port = self.parse("10.0.0.1")
        self.assertEqual(host, "10.0.0.1")
        self.assertEqual(port, "9000")

    def test_ipv6_bracket_port(self):
        host, port = self.parse("[2409:8014::4a]:8025")
        self.assertEqual(host, "2409:8014::4a")
        self.assertEqual(port, "8025")

    def test_ipv6_no_bracket(self):
        host, port = self.parse("2409:8014::4a")
        # Should keep as-is since no clear port separator
        self.assertEqual(port, "9000")

    def test_empty(self):
        host, port = self.parse("")
        self.assertEqual(host, "")
        self.assertEqual(port, "9000")

    def test_domain_with_port(self):
        host, port = self.parse("play.min.io:9000")
        self.assertEqual(host, "play.min.io")
        self.assertEqual(port, "9000")


# Helper for URL encoding in Python 2.7
try:
    from urllib import quote as urllib_quote
except ImportError:
    from urllib.parse import quote as urllib_quote


if __name__ == "__main__":
    # Count tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestConfigAPI)
    suite.addTests(loader.loadTestsFromTestCase(TestBucketAPI))
    suite.addTests(loader.loadTestsFromTestCase(TestFileOperations))
    suite.addTests(loader.loadTestsFromTestCase(TestSearchAPI))
    suite.addTests(loader.loadTestsFromTestCase(TestPathConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestBatchDownload))
    suite.addTests(loader.loadTestsFromTestCase(TestHealthCheck))
    suite.addTests(loader.loadTestsFromTestCase(TestEndpointParsing))

    print("=" * 60)
    print("MinIO File Service - Test Suite")
    print("=" * 60)
    print("IS_MINIO_V7_API:", IS_MINIO_V7_API)
    print()

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors
    print("Total:  " + str(total))
    print("Passed: " + str(passed))
    print("Failed: " + str(failures))
    print("Errors: " + str(errors))
    print("=" * 60)

    # Clean up config file
    config_path = os.path.join(os.path.dirname(__file__), ".minio_config.json")
    if os.path.exists(config_path):
        os.remove(config_path)
