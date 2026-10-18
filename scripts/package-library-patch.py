"""Package this integration and verify it against pristine files at the pinned base."""
import json
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'artifacts/librechat-upstream'
OUTPUT = ROOT / 'integrations/librechat/library.patch'
BASE = 'e8f3be08623663d4ad7f7241e693c94469b63bb0'


def git(*args, cwd=UPSTREAM, allowed=(0,)):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True)
    if result.returncode not in allowed:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result.stdout


assert git('rev-parse', 'HEAD').decode().strip() == BASE
tracked = git('diff', '--name-only', '-z', BASE).decode().strip('\0').split('\0')
new = git('ls-files', '--others', '--exclude-standard', '-z').decode().strip('\0').split('\0')
tracked = [p for p in tracked if p]
new = [p for p in new if p]
allowed_new = ('api/server/routes/library.js', 'client/src/components/Library/',
               'packages/api/src/library/', 'packages/data-provider/src/library.ts')
assert all(p.startswith(allowed_new) for p in new), 'Unexpected untracked upstream file'
patch = git('diff', '--binary', BASE)
for path in new:
    patch += git('diff', '--binary', '--no-index', '--', '/dev/null', path, allowed=(1,))
OUTPUT.write_bytes(patch)
with tempfile.TemporaryDirectory(prefix='library-patch-check-') as temp:
    for path in tracked:
        target = pathlib.Path(temp) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git('show', f'{BASE}:{path}'))
    git('apply', '--check', str(OUTPUT), cwd=temp)
    git('apply', str(OUTPUT), cwd=temp)
    for path in tracked + new:
        actual = (pathlib.Path(temp) / path).read_bytes().replace(b'\r\n', b'\n')
        expected = (UPSTREAM / path).read_bytes().replace(b'\r\n', b'\n')
        assert actual == expected, path
manifest = {'base': BASE, 'files': tracked + new, 'pristine_apply_verified': True}
(ROOT / 'integrations/librechat/library-patch-manifest.json').write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(f'PASS: clean apply and content equality for {len(tracked + new)} files')
