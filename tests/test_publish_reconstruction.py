import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'publish_reconstruction.py'
spec = importlib.util.spec_from_file_location('publish_reconstruction', SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def gaussian_bytes(properties=None, count=2):
    names = list(properties if properties is not None else sorted(publisher.REQUIRED_PROPERTIES))
    header = '\n'.join(['ply', 'format binary_little_endian 1.0', f'element vertex {count}',
                        *(f'property float {name}' for name in names), 'end_header', '']).encode('ascii')
    return header + struct.pack('<' + 'f' * (len(names) * count), *([1.0] * (len(names) * count)))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / 'repo'
        self.source = self.root / 'team-result.ply'
        self.source.write_bytes(gaussian_bytes())
        self.destination = self.repository / 'reconstruction' / 'latest.ply'
        self.manifest = self.repository / 'reconstruction' / 'latest.json'

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_publish_preserves_source_and_uses_only_its_filename(self):
        source_bytes = self.source.read_bytes()
        result = publisher.publish(self.source, self.repository, '딸기 새 결과')
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.destination.read_bytes(), source_bytes)
        self.assertEqual(json.loads(self.manifest.read_text()), result)
        self.assertEqual(result['sha256'], hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(result['vertexCount'], 2)
        self.assertEqual(result['name'], '딸기 새 결과')
        self.assertEqual(result['path'], 'reconstruction/latest.ply')
        self.assertEqual(result['sourceProvidedFile'], self.source.name)
        self.assertNotIn(str(self.root), self.manifest.read_text())
        self.assertEqual(sorted(p.name for p in self.destination.parent.iterdir()), ['latest.json', 'latest.ply'])

    def test_republishing_latest_itself_does_not_truncate_the_input(self):
        first = publisher.publish(self.source, self.repository)
        second = publisher.publish(self.destination, self.repository)
        self.assertEqual(second['sha256'], first['sha256'])
        self.assertEqual(second['sizeBytes'], first['sizeBytes'])

    def test_invalid_inputs_preserve_published_bytes_and_metadata(self):
        publisher.publish(self.source, self.repository)
        original_bytes, original_manifest = self.destination.read_bytes(), self.manifest.read_bytes()
        valid = gaussian_bytes()
        invalid = [b'not a PLY', valid[:-1], valid + b'extra',
                   valid.replace(b'binary_little_endian', b'ascii'),
                   gaussian_bytes(['x', 'y', 'z']),
                   gaussian_bytes(count=0),
                   valid.replace(b'property float x', b'property double x'),
                   valid.replace(b'end_header', b'element face 1\nproperty list uchar int vertex_indices\nend_header'),
                   b'ply\n' + b'x' * publisher.MAX_HEADER_BYTES]
        for data in invalid:
            with self.subTest(data=data[:40]):
                self.source.write_bytes(data)
                with self.assertRaises(ValueError):
                    publisher.publish(self.source, self.repository)
                self.assertEqual(self.destination.read_bytes(), original_bytes)
                self.assertEqual(self.manifest.read_bytes(), original_manifest)

    def test_failed_copy_or_changed_source_cannot_replace_existing_result(self):
        publisher.publish(self.source, self.repository)
        previous = self.destination.read_bytes(), self.manifest.read_bytes()
        def changed_copy(source, target, **kwargs):
            target.write(gaussian_bytes(count=3))
        with patch.object(publisher.shutil, 'copyfileobj', side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                publisher.publish(self.source, self.repository)
        self.assertEqual((self.destination.read_bytes(), self.manifest.read_bytes()), previous)
        self.assertEqual(sorted(p.name for p in self.destination.parent.iterdir()), ['latest.json', 'latest.ply'])

    def test_bootstrap_provenance_is_kept_only_for_the_same_content(self):
        first = publisher.publish(self.source, self.repository)
        bootstrap = {'repository': 'initial-repo', 'sha256': first['sha256']}
        first['bootstrapSource'] = bootstrap
        self.manifest.write_text(json.dumps(first))
        self.assertEqual(publisher.publish(self.source, self.repository)['bootstrapSource'], bootstrap)
        self.source.write_bytes(gaussian_bytes(count=3))
        self.assertNotIn('bootstrapSource', publisher.publish(self.source, self.repository))

    def test_existing_publish_lock_prevents_concurrent_replacement(self):
        publisher.publish(self.source, self.repository)
        self.destination.parent.joinpath('.publish.lock').touch()
        with self.assertRaisesRegex(ValueError, 'already running'):
            publisher.publish(self.source, self.repository)

    def test_invalid_names_and_duplicate_properties_are_rejected(self):
        for name in ['', 'a\nb', 'x' * 121]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    publisher.publish(self.source, self.repository, name)
        self.source.write_bytes(gaussian_bytes([*sorted(publisher.REQUIRED_PROPERTIES), 'x']))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            publisher.inspect_ply(self.source)


if __name__ == '__main__':
    unittest.main()
