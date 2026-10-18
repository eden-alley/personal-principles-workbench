"""Create a fresh source-only export. Never copy runtime/user data or Git history."""
import argparse
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('config', 'docs', 'integrations', 'scripts', 'tests', 'workbench')
FILES = ('.env.example', '.gitignore', '.gitattributes', 'manage.py', 'PRODUCT.md', 'README.md',
         'requirements.txt', 'setup.ps1', 'start.ps1', 'test.ps1', 'THIRD_PARTY_NOTICES.md')
BLOCKED = {'.git', '.venv', '__pycache__', 'node_modules', 'data', 'artifacts', '.pytest_cache'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    destination = parser.parse_args().destination.resolve()
    if destination.exists():
        raise SystemExit('Destination must be a new directory.')
    sources = [ROOT / name for name in FILES]
    for name in DIRECTORIES:
        def walk(folder):
            for path in folder.iterdir():
                if path.is_symlink() or path.name in BLOCKED:
                    continue
                if path.is_dir():
                    yield from walk(path)
                elif path.suffix not in {'.pyc', '.log', '.sqlite3', '.pem', '.key'} and not path.name.startswith('.env'):
                    yield path
        sources.extend(walk(ROOT / name))
    # Fail closed for recognizable live credential formats; report filenames only.
    secret = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
    for source in sources:
        if secret.search(source.read_bytes()):
            raise SystemExit('Potential credential in ' + source.relative_to(ROOT).as_posix())
    destination.mkdir(parents=True)
    for source in sources:
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    print(json.dumps({'files': len(sources), 'destination': str(destination)}))


if __name__ == '__main__':
    main()
