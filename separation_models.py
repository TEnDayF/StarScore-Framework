"""Approved local UMXHQ weights. No implicit downloads or legacy fallback.

Fabian-Robert Stoter and Antoine Liutkus, Open-Unmix UMXHQ, MIT.
Origin: https://zenodo.org/records/3370489 (not the differently licensed UMXL).
"""
import hashlib
from pathlib import Path

WEIGHTS = {
    'bass': ('bass-8d85a5bd.pth', '8d85a5bd3f996a8867fca0e8442e077e5a3f5ec747a6112742452a8f347b39c8'),
    'drums': ('drums-9619578f.pth', '9619578f885c54737cb0234f9f9a4a679ee4f31438fd77fd1dbe02bb16c2da0a'),
    'other': ('other-b52fbbf7.pth', 'b52fbbf76479e752bd72e02304c602ac7802aa5bfbfb9cd12054b2695d5093ab'),
    'vocals': ('vocals-b62c91ce.pth', 'b62c91cedbc7a066f1778ead5b5cecb377aa3a46a31af1cce7c5c8769339d083'),
}


def verify(folder):
    paths = {}
    for stem, (name, expected) in WEIGHTS.items():
        path = Path(folder) / name
        if not path.is_file():
            raise FileNotFoundError('UMXHQ model missing: ' + str(path))
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('UMXHQ SHA-256 mismatch: ' + name)
        paths[stem] = path
    return paths
