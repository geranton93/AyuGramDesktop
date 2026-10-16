#!/usr/bin/env python3
"""Read-only prerequisites and explicitly scoped static analysis; never builds."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
ANALYZERS = ('clang-tidy', 'clazy-standalone', 'cppcheck')


def find_tool(name):
    candidates = []
    if os.environ.get('LLVM_BIN'):
        candidates.append(Path(os.environ['LLVM_BIN']) / name)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    if sys.platform == 'darwin':
        candidates.extend(Path(prefix) / name for prefix in (
            '/opt/homebrew/opt/llvm/bin', '/usr/local/opt/llvm/bin'))
    return next((str(path) for path in candidates
                 if path.is_file() and os.access(path, os.X_OK)), None)


def require_tool(name):
    result = find_tool(name)
    if not result:
        raise FileNotFoundError(f'Required tool unavailable: {name}')
    return result


def select_entries(entries, files):
    if not files:
        raise ValueError('Select at least one translation unit with --file.')
    requested = {Path(file).resolve() for file in files}
    selected = []
    found = set()
    for entry in entries:
        path = (Path(entry['directory']) / entry['file']).resolve()
        if path in requested:
            selected.append(entry)
            found.add(path)
    missing = requested - found
    if missing:
        raise ValueError('Not present in compilation database: '
                         + ', '.join(str(path) for path in sorted(missing)))
    return selected


def doctor(database):
    missing = []
    for name in ('cmake', 'ninja', 'clang++', *ANALYZERS, 'scan-build', 'codegraph'):
        path = find_tool(name)
        if not path:
            print(f'MISSING {name}')
            missing.append(name)
            continue
        argument = '-h' if name == 'scan-build' else '--version'
        result = subprocess.run([path, argument], capture_output=True, text=True, timeout=30)
        lines = (result.stdout or result.stderr).splitlines()
        summary = next((line for line in lines if line.strip()), 'version unavailable')
        state = 'FOUND' if result.returncode == 0 else 'BROKEN'
        print(f'{state} {name}: {path} ({summary})')
        if result.returncode:
            missing.append(name)
    if not database.is_file():
        print(f'MISSING compilation database: {database}')
        missing.append('compilation database')
    else:
        entries = json.loads(database.read_text())
        print(f'FOUND compilation database: {len(entries)} entries; freshness must be verified')
        if not entries:
            missing.append('nonempty compilation database')
    print('No application or dependency builds were performed.')
    return 2 if missing else 0


def analyze(args):
    database = args.database.resolve()
    entries = select_entries(json.loads(database.read_text()), args.file)
    tool = require_tool(args.tool)
    with tempfile.TemporaryDirectory(prefix='ayugram-analysis-') as directory:
        filtered = Path(directory) / 'compile_commands.json'
        filtered.write_text(json.dumps(entries))
        if args.tool == 'cppcheck':
            return subprocess.run([
                tool, f'--project={filtered}', '--enable=warning,performance,portability,missingInclude',
                '--error-exitcode=1', '--inline-suppr', '--check-level=exhaustive',
            ], cwd=ROOT).returncode
        result = 0
        for source in sorted({str((Path(e['directory']) / e['file']).resolve())
                              for e in entries}):
            command = [tool, '-p', str(filtered.parent), source]
            if args.tool == 'clang-tidy':
                command += [f'--config-file={ROOT / ".clang-tidy"}', '--warnings-as-errors=*']
            else:
                command += ['-checks=level0,level1', '--extra-arg=-Werror']
            code = subprocess.run(command, cwd=ROOT).returncode
            result = result or code
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('doctor', 'analyze'))
    parser.add_argument('--database', type=Path,
                        default=ROOT / 'out/quality-static/compile_commands.json')
    parser.add_argument('--tool', choices=ANALYZERS, default='clang-tidy')
    parser.add_argument('--file', action='append', default=[])
    args = parser.parse_args()
    try:
        return doctor(args.database) if args.action == 'doctor' else analyze(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f'UNVERIFIED: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
