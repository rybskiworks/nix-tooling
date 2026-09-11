import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('run_checks', HERE / 'run_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class NixChecksTests(unittest.TestCase):
    def fixture(self):
        return {'z-last': '/nix/store/' + 'a' * 32 + '-check.drv',
                'a-first': '/nix/store/' + 'b' * 32 + '-check.drv'}

    def test_names_are_sorted(self):
        self.assertEqual(checks.selected_checks(self.fixture()),
                         ['.#checks.x86_64-linux.a-first', '.#checks.x86_64-linux.z-last'])

    def test_invalid_or_empty_outputs_fail_closed(self):
        for data in [{}, [], {'--arg': '/nix/store/' + 'a' * 32 + '-check.drv'},
                     {'check': '/tmp/not-a-derivation'}, {'check': None}]:
            with self.assertRaises(ValueError):
                checks.selected_checks(data)

    def test_eval_does_not_build_or_override_devenv_root(self):
        with patch.object(checks.subprocess, 'check_output', return_value=json.dumps(self.fixture())) as evaluate, \
                patch.object(checks.subprocess, 'run') as execute:
            checks.run('evaluate')
            command = evaluate.call_args.args[0]
            self.assertIn('.#checks.x86_64-linux', command)
            self.assertIn('--no-update-lock-file', command)
            self.assertNotIn('--override-input', command)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[0][0], 'git')

    def test_full_builds_exactly_the_validated_checks(self):
        with patch.object(checks.subprocess, 'check_output', return_value=json.dumps(self.fixture())), \
                patch.object(checks.subprocess, 'run') as execute:
            checks.run('full')
            command = execute.call_args_list[0].args[0]
            self.assertEqual(command[:2], ['nix', 'build'])
            self.assertIn('--no-link', command)
            self.assertEqual(command[-2:], checks.selected_checks(self.fixture()))
            self.assertTrue(execute.call_args_list[0].kwargs['check'])

    def test_build_failure_propagates(self):
        with patch.object(checks.subprocess, 'check_output', return_value=json.dumps(self.fixture())), \
                patch.object(checks.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'nix')):
            with self.assertRaises(subprocess.CalledProcessError):
                checks.run('full')


if __name__ == '__main__':
    unittest.main()
