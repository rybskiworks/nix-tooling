#!/usr/bin/env python3
"""Evaluate/build ordinary Nix checks without evaluating the interactive devshell.

No overridden root file, lock update, shell entry, hook installation, image build
or optional KVM test is implied. Full means all exposed checks for this system.
"""
import argparse
import json
import re
import subprocess

SYSTEM = 'x86_64-linux'
COMMON = ['--no-update-lock-file', '--option', 'allow-import-from-derivation', 'false']


def selected_checks(value: object) -> list[str]:
    if not isinstance(value, dict) or not value:
        raise ValueError('Expected a nonempty set of ordinary check derivations')
    for name, drv in value.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', name):
            raise ValueError(f'Unsafe or unsupported check name: {name!r}')
        if not isinstance(drv, str) or not re.fullmatch(r'/nix/store/[a-z0-9]{32}-[^/\n]+\.drv', drv):
            raise ValueError(f'Expected a derivation path for {name}')
    return [f'.#checks.{SYSTEM}.{name}' for name in sorted(value)]


def run(tier: str) -> None:
    if tier not in {'evaluate', 'full'}:
        raise ValueError('Expected evaluate or full')
    output = subprocess.check_output(
        ['nix', 'eval', '--json', *COMMON, f'.#checks.{SYSTEM}', '--apply',
         'checks: builtins.mapAttrs (_: check: check.drvPath) checks'], text=True)
    checks = selected_checks(json.loads(output))
    print(f'Ordinary checks ({tier}): ' + ', '.join(checks), flush=True)
    if tier == 'full':
        subprocess.run(['nix', 'build', '--no-link', '--keep-going', '--print-build-logs',
                        *COMMON, *checks], check=True)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', 'flake.lock'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tier', choices=['evaluate', 'full'])
    run(parser.parse_args().tier)
