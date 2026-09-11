#!/usr/bin/env python3
"""Explicit Nix CI tiers and unpublished SemVer metadata. No publishing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def version(root: Path = ROOT) -> str:
    text = (root / 'version.txt').read_text()
    number = r'(?:0|[1-9][0-9]*)'
    pattern = rf'{number}\.{number}\.{number}(?:-(?:alpha|beta|rc)\.{number})?\n'
    if re.fullmatch(pattern, text) is None:
        raise ValueError('version.txt must contain one canonical SemVer line')
    return text.rstrip('\n')


def changed_paths(event_name: str, event: dict) -> list[str] | None:
    if event_name != 'pull_request':
        return None
    pr = event['pull_request']
    base, head = pr['base']['sha'], pr['head']['sha']
    if not all(re.fullmatch('[0-9a-f]{40}', value) and set(value) != {'0'} for value in (base, head)):
        return None
    try:
        output = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z',
                                          f'{base}...{head}', '--'], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None
    return [name.decode('utf-8', 'surrogateescape') for name in output.split(b'\0') if name]


def full_selected(event_name: str, event: dict, paths: list[str] | None = None) -> bool:
    if event_name in {'push', 'merge_group', 'workflow_dispatch'}:
        return True
    if any(label.get('name') == 'nix-ci' for label in event.get('pull_request', {}).get('labels', [])):
        return True
    # Only known narrative docs may skip compiled checks. Unknown diffs fail closed.
    def docs_only(path: str) -> bool:
        return path.endswith('.md') and (path.startswith('docs/') or '/' not in path)
    return paths is None or not paths or any(not docs_only(path) for path in paths)


def gate(needs: dict) -> None:
    if set(needs) != {'policy', 'nix-eval', 'nix-full'}:
        raise ValueError('Unknown or missing CI dependency')
    selected = needs['policy'].get('outputs', {}).get('full')
    if selected not in {'true', 'false'}:
        raise ValueError('Missing full-check selection')
    for name in ('policy', 'nix-eval'):
        if needs[name].get('result') != 'success':
            raise ValueError(f'{name} did not succeed')
    result = needs['nix-full'].get('result')
    if result != 'success' and not (selected == 'false' and result == 'skipped'):
        raise ValueError('Selected full Nix checks did not succeed')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'metadata', 'select', 'gate'}:
        raise SystemExit('usage: contract.py metadata|select|gate')
    if sys.argv[1] == 'metadata':
        print(f'Tooling source version: {version()} (not a published release)')
    elif sys.argv[1] == 'select':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        name = os.environ['GITHUB_EVENT_NAME']
        full = str(full_selected(name, event, changed_paths(name, event))).lower()
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'full={full}\n')
        print(f'Full ordinary checks selected: {full}; evaluation alone is not a runtime test.')
    else:
        gate(json.loads(os.environ['NEEDS_JSON']))
        print('All selected ordinary checks succeeded; optional native/KVM tests are separate.')
