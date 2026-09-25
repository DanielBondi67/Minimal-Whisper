import contextlib
import hashlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.model_download import download_model


class ModelDownloadTests(unittest.TestCase):
    payload = b'whisper checkpoint test data\n' * 4096

    class Response(io.BytesIO):
        def __init__(self, body, status, headers):
            super().__init__(body)
            self.status = status
            self.headers = headers

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def make_url(self, payload=None):
        payload = self.payload if payload is None else payload
        digest = hashlib.sha256(payload).hexdigest()
        return f'https://models.example/{digest}/model.pt'

    def test_resumes_interrupted_download_with_http_range(self):
        url = self.make_url()
        calls = []

        def open_response(request, timeout):
            byte_range = request.get_header('Range')
            calls.append(byte_range)
            if byte_range:
                start = int(byte_range.split('=', 1)[1].split('-', 1)[0])
                return self.Response(
                    self.payload[start:], 206,
                    {'Content-Range': f'bytes {start}-{len(self.payload) - 1}/{len(self.payload)}'})
            return self.Response(
                self.payload[:len(self.payload) // 2], 200,
                {'Content-Length': str(len(self.payload))})

        with TemporaryDirectory() as directory:
            destination = Path(directory) / 'model.pt'
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(ConnectionError):
                    with patch('src.model_download.urlopen', side_effect=open_response):
                        download_model(url, destination)
                partial = Path(str(destination) + '.partial')
                saved_bytes = partial.stat().st_size
                self.assertGreater(saved_bytes, 0)
                self.assertLess(saved_bytes, len(self.payload))

                with patch('src.model_download.urlopen', side_effect=open_response):
                    result = download_model(url, destination)

            self.assertEqual(result, str(destination))
            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertFalse(partial.exists())
            self.assertEqual(calls, [None, f'bytes={saved_bytes}-'])

    def test_restarts_cleanly_when_server_ignores_range(self):
        url = self.make_url()
        calls = []

        def open_response(request, timeout):
            byte_range = request.get_header('Range')
            calls.append(byte_range)
            if byte_range:
                return self.Response(self.payload, 200,
                                     {'Content-Length': str(len(self.payload))})
            return self.Response(
                self.payload[:len(self.payload) // 2], 200,
                {'Content-Length': str(len(self.payload))})

        with TemporaryDirectory() as directory:
            destination = Path(directory) / 'model.pt'
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(ConnectionError):
                    with patch('src.model_download.urlopen', side_effect=open_response):
                        download_model(url, destination)
                with patch('src.model_download.urlopen', side_effect=open_response):
                    download_model(url, destination)

            self.assertEqual(destination.read_bytes(), self.payload)
            self.assertEqual(len(calls), 2)
            self.assertIsNone(calls[0])
            self.assertTrue(calls[1].startswith('bytes='))

    def test_rejects_checksum_mismatch_without_promoting_partial(self):
        wrong = b'wrong model bytes'
        url = self.make_url(self.payload)

        def open_response(_request, timeout):
            return self.Response(wrong, 200, {'Content-Length': str(len(wrong))})

        with TemporaryDirectory() as directory:
            destination = Path(directory) / 'model.pt'
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                    with patch('src.model_download.urlopen', side_effect=open_response):
                        download_model(url, destination)
            self.assertFalse(destination.exists())
            self.assertTrue(Path(str(destination) + '.partial').exists())


if __name__ == '__main__':
    unittest.main()
