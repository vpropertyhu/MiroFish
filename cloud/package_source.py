"""Build the downloadable corresponding source without data, keys or itself."""
from pathlib import Path
import sys
import zipfile

def package(root, destination):
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    ignored = {'.git', '.venv', 'node_modules', 'dist', 'uploads', 'logs', '__pycache__', '.pytest_cache', 'ellenorzes', 'mentesek'}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path == destination or path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in ignored for part in rel.parts):
                continue
            if (path.name.startswith('.env') and path.name != '.env.example') or path.suffix in {'.pem', '.key', '.pyc', '.db', '.sqlite3', '.zip'}:
                continue
            archive.writestr(rel.as_posix(), path.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None

if __name__ == '__main__':
    package(sys.argv[1], sys.argv[2])
