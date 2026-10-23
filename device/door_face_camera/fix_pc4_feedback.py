"""Apply the CRLF feedback fix to the V-protocol bridge running on PC4.

Usage on PC4: python3 fix_pc4_feedback.py /path/to/your/bridge.py
Creates a .before-crlf-fix backup; restart the bridge after applying.
"""
from pathlib import Path
import argparse
import re
import shutil


def patched_source(source: str) -> str:
    pattern = re.compile(
        r'^(?P<indent>[ \t]*)if feedback\.startswith\(b[\'"]DOOR,[\'"]\) and '
        r'feedback in \(b[\'"]DOOR,0[\'"], b[\'"]DOOR,1[\'"]\):', re.MULTILINE)
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError("Expected exactly one DOOR feedback check; file was not changed.")
    match = matches[0]
    normalization = match['indent'] + 'feedback = feedback.rstrip(b"\\r")\n'
    if source[:match.start()].endswith(normalization):
        return source
    result = source[:match.start()] + normalization + source[match.start():]
    compile(result, '<patched PC4 bridge>', 'exec')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bridge', type=Path)
    args = parser.parse_args()
    source = args.bridge.read_text(encoding='utf-8')
    result = patched_source(source)
    if result == source:
        print('Already fixed; no change.')
        return
    backup = args.bridge.with_name(args.bridge.name + '.before-crlf-fix')
    if backup.exists():
        raise SystemExit(f'Backup already exists: {backup}; file was not changed.')
    shutil.copy2(args.bridge, backup)
    args.bridge.write_text(result, encoding='utf-8')
    print(f'Fixed: {args.bridge}\nBackup: {backup}\nRestart the PC4 bridge.')


if __name__ == '__main__':
    main()
