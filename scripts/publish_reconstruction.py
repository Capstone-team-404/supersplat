#!/usr/bin/env python3
"""Validate and publish a teammate-produced Gaussian PLY for the Unity integration."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


REQUIRED_PROPERTIES = {
    'x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
    'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3',
}
MAX_HEADER_BYTES = 64 * 1024
LATEST_PATH = 'reconstruction/latest.ply'


def inspect_ply(path):
    """Check the standard, uncompressed float32 3DGS PLY layout and hash its bytes."""
    path = Path(path)
    if path.suffix.lower() != '.ply':
        raise ValueError('The source must be a .ply file')
    if not path.is_file():
        raise ValueError('The source PLY does not exist or is not a regular file')
    with path.open('rb') as stream:
        header = bytearray()
        while True:
            line = stream.readline(MAX_HEADER_BYTES + 1 - len(header))
            header.extend(line)
            if not line or len(header) > MAX_HEADER_BYTES:
                raise ValueError('PLY header is missing end_header or exceeds 64 KiB')
            if line.rstrip(b'\r\n') == b'end_header':
                break
        try:
            lines = header.decode('ascii').splitlines()
        except UnicodeDecodeError:
            raise ValueError('PLY header must contain ASCII text') from None
        if len(lines) < 4 or lines[0] != 'ply' or lines[1] != 'format binary_little_endian 1.0':
            raise ValueError('Expected a binary_little_endian 1.0 PLY')
        vertex_count = None
        properties = []
        for line in lines[2:-1]:
            fields = line.split()
            if not fields:
                raise ValueError('Unexpected blank PLY header line')
            if fields[0] in ('comment', 'obj_info'):
                continue
            if fields[0] == 'element':
                if len(fields) != 3 or fields[1] != 'vertex' or vertex_count is not None:
                    raise ValueError('Only one vertex element is supported; mesh faces are not 3DGS data')
                try:
                    vertex_count = int(fields[2])
                except ValueError:
                    raise ValueError('Invalid vertex count') from None
                if vertex_count <= 0:
                    raise ValueError('The PLY must contain at least one Gaussian')
            elif fields[0] == 'property':
                if vertex_count is None or len(fields) != 3 or fields[1] not in ('float', 'float32'):
                    raise ValueError('Gaussian vertex properties must be scalar float32 values')
                if fields[2] in properties:
                    raise ValueError('Duplicate PLY vertex property')
                properties.append(fields[2])
            else:
                raise ValueError('Unsupported PLY header directive')
        missing = REQUIRED_PROPERTIES - set(properties)
        if vertex_count is None or missing:
            raise ValueError('Missing Gaussian properties: ' + ', '.join(sorted(missing)))
        expected_size = len(header) + vertex_count * len(properties) * 4
        digest = hashlib.sha256(header)
        size = len(header)
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
        if size != expected_size:
            raise ValueError('PLY payload length does not match its vertex count and float32 properties')
    return {'sha256': digest.hexdigest(), 'vertexCount': vertex_count, 'sizeBytes': size}


@contextmanager
def publish_lock(directory):
    lock = directory / '.publish.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('A publish is already running (.publish.lock exists)') from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def publish(source, repository, name=None):
    source = Path(source)
    # Reject malformed inputs before making any change to the published artifacts.
    expected = inspect_ply(source)
    directory = Path(repository) / 'reconstruction'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / 'latest.ply'
    manifest_path = directory / 'latest.json'
    with publish_lock(directory):
        previous = {}
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(previous, dict):
                raise ValueError('Existing latest.json must contain a JSON object')
        display_name = name if name is not None else previous.get('name', source.stem)
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120 or any(ord(c) < 32 for c in display_name):
            raise ValueError('Display name must contain 1-120 characters without control characters')
        manifest = {
            'schemaVersion': 1,
            'path': LATEST_PATH,
            'name': display_name,
            **expected,
            'sourceProvidedFile': source.name,
            'updatedAt': datetime.now(timezone.utc).isoformat(),
        }
        bootstrap = previous.get('bootstrapSource')
        if isinstance(bootstrap, dict) and bootstrap.get('sha256') == expected['sha256']:
            manifest['bootstrapSource'] = bootstrap
        ply_temp = metadata_temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.ply', prefix='.publish-', dir=directory, delete=False) as copied:
                ply_temp = Path(copied.name)
                with source.open('rb') as original:
                    shutil.copyfileobj(original, copied, length=1024 * 1024)
                copied.flush()
                os.fsync(copied.fileno())
            if inspect_ply(ply_temp) != expected:
                raise ValueError('Source changed while copying; published files were preserved')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', prefix='.publish-', dir=directory, delete=False) as metadata:
                metadata_temp = Path(metadata.name)
                json.dump(manifest, metadata, ensure_ascii=False, indent=2)
                metadata.write('\n')
                metadata.flush()
                os.fsync(metadata.fileno())
            # Each replacement is atomic. The hash in the manifest protects consumers
            # against a crash between replacements; publish both files in one Git commit.
            os.replace(ply_temp, destination)
            os.replace(metadata_temp, manifest_path)
        finally:
            for temporary in (ply_temp, metadata_temp):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='A completed uncompressed Gaussian .ply result')
    parser.add_argument('--name', help='Name shown in the integrated web dashboard')
    parser.add_argument('--check', action='store_true', help='Validate only; do not replace published files')
    args = parser.parse_args(argv)
    try:
        result = inspect_ply(args.source) if args.check else publish(args.source, Path(__file__).resolve().parents[1], args.name)
    except (OSError, ValueError) as exc:
        print('Publish failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
