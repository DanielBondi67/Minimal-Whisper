"""Resumable, checksum-verified downloads for OpenAI Whisper checkpoints."""

import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _report_progress(done, total, last_reported):
    if not total:
        return last_reported
    percent = min(100, done * 100 // total)
    if percent != last_reported:
        print(f'{percent}%', file=sys.stderr, flush=True)
    return percent


def download_model(url, destination, timeout=60):
    """Download a Whisper checkpoint, resuming its .partial file when possible.

    Existing Whisper cache files are retained when valid. Invalid old-style
    final files are moved into the resumable partial path before retrying.
    Partial downloads are only promoted to the model filename after their
    SHA-256 digest matches the digest embedded in the official Whisper URL.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_sha256 = url.rstrip('/').split('/')[-2]
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256):
        raise ValueError('The model URL does not contain a valid SHA-256 checksum.')

    partial = Path(str(destination) + '.partial')
    if destination.exists():
        if not destination.is_file():
            raise RuntimeError(f'{destination} exists and is not a regular file.')
        if _sha256(destination) == expected_sha256:
            return str(destination)
        if not partial.exists():
            os.replace(destination, partial)
        else:
            destination.unlink()

    # A server may reject Range at end-of-file (416), or ignore it and return
    # the full object (200). In either case retrying from byte zero is safe.
    for attempt in range(2):
        offset = partial.stat().st_size if partial.is_file() else 0
        headers = {'Range': f'bytes={offset}-'} if offset else {}
        request = Request(url, headers=headers)
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as exc:
            if offset and exc.code == 416:
                partial.unlink(missing_ok=True)
                continue
            raise

        with response:
            status = getattr(response, 'status', response.getcode())
            content_range = response.headers.get('Content-Range', '')
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)', content_range)
            if status == 206:
                if not match or int(match.group(1)) != offset:
                    response.close()
                    partial.unlink(missing_ok=True)
                    if attempt == 0:
                        continue
                    raise RuntimeError('The server returned an invalid byte range.')
                range_start, range_end = int(match.group(1)), int(match.group(2))
                expected_bytes = range_end - range_start + 1
                total = (int(match.group(3)) if match.group(3) != '*'
                         else offset + expected_bytes)
                mode = 'ab'
                completed = offset
            elif status == 200:
                expected_bytes = response.headers.get('Content-Length')
                expected_bytes = int(expected_bytes) if expected_bytes else None
                total = expected_bytes
                mode = 'wb'
                completed = 0
            else:
                raise RuntimeError(f'The model server returned HTTP {status}.')

            last_reported = _report_progress(completed, total, -1)
            received = 0
            with partial.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    completed += len(chunk)
                    last_reported = _report_progress(completed, total, last_reported)
                output.flush()
                os.fsync(output.fileno())

            if expected_bytes is not None and received != expected_bytes:
                raise ConnectionError(
                    f'The download stopped early ({received} of {expected_bytes} bytes). '
                    'The saved data can be resumed by trying again.')

        if _sha256(partial) != expected_sha256:
            raise RuntimeError(
                'The downloaded model failed its SHA-256 check. Retry the download; '
                'an incomplete transfer will resume automatically.')
        os.replace(partial, destination)
        print('100%', file=sys.stderr, flush=True)
        return str(destination)

    raise RuntimeError('Could not resume the model download; try again.')
