#!/usr/bin/env python3
"""Explicit Nix CI tiers and unpublished SemVer metadata. No network or publishing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def version(root: Path = ROOT) -> str:
    text = (root / 'version.txt').read_text()
    number = r'(?:0|[1-9][0-9]*)'
    pattern = rf'{number}\.{number}\.{number}(?:-(?:alpha|beta|rc)\.{number})?\n'
    if re.fullmatch(pattern, text) is None:
        raise ValueError('version.txt must contain one canonical SemVer line')
    return text.rstrip('\n')


def full_selected(event_name: str, event: dict) -> bool:
    return event_name == 'workflow_dispatch' or any(
        label.get('name') == 'nix-ci' for label in event.get('pull_request', {}).get('labels', [])
    )


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
        full = str(full_selected(os.environ['GITHUB_EVENT_NAME'], event)).lower()
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'full={full}\n')
        print(f'Full compiled checks selected: {full}; evaluation alone is not a runtime test.')
    else:
        gate(json.loads(os.environ['NEEDS_JSON']))
        print('All selected checks succeeded. Evaluation-only runs do not qualify a release.')
