"""Offline regression tests for verified CI tool transport resilience."""
import importlib.util
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_ci_tools.py"
spec = importlib.util.spec_from_file_location("ci_scanner_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def http_error(code):
    return urllib.error.HTTPError("https://github.com/example/release", code, "download failure", {}, None)


class VerifiedScannerDownloads(unittest.TestCase):
    def test_retries_temporary_gateway_error_and_replaces_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.tar"
            target.write_bytes(b"partial-old-download")
            with patch.object(installer.urllib.request, "urlopen", side_effect=[
                http_error(504), io.BytesIO(b"correct-pinned-asset")
            ]) as request, patch.object(installer.time, "sleep") as pause:
                installer.download("https://github.com/example/release", target)
            self.assertEqual(target.read_bytes(), b"correct-pinned-asset")
            self.assertEqual(request.call_count, 2)
            pause.assert_called_once()

    def test_permanent_http_errors_fail_without_retry_or_stale_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.tar"
            target.write_bytes(b"stale")
            with patch.object(installer.urllib.request, "urlopen", side_effect=http_error(403)) as request, patch.object(installer.time, "sleep") as pause:
                with self.assertRaises(urllib.error.HTTPError):
                    installer.download("https://github.com/example/release", target)
            self.assertFalse(target.exists())
            self.assertEqual(request.call_count, 1)
            pause.assert_not_called()

    def test_bounded_retries_fail_closed_and_do_not_leave_partials(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.tar"
            with patch.object(installer.urllib.request, "urlopen", side_effect=http_error(503)) as request, patch.object(installer.time, "sleep") as pause:
                with self.assertRaises(urllib.error.HTTPError):
                    installer.download("https://github.com/example/release", target)
            self.assertFalse(target.exists())
            self.assertEqual(request.call_count, 4)
            self.assertEqual(pause.call_count, 3)

    def test_temporary_connection_failures_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.tar"
            with patch.object(installer.urllib.request, "urlopen", side_effect=[
                urllib.error.URLError("connection reset"), io.BytesIO(b"fixed")
            ]) as request, patch.object(installer.time, "sleep"):
                installer.download("https://github.com/example/release", target)
            self.assertEqual(target.read_bytes(), b"fixed")
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
