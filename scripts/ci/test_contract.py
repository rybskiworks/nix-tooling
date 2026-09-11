import copy
from pathlib import Path
import tempfile
import unittest
import contract

class ContractTests(unittest.TestCase):
    def test_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ['0.1.0\n', '1.2.3\n', '0.1.0-rc.1\n']:
                (root/'version.txt').write_text(value)
                self.assertEqual(contract.version(root), value.rstrip('\n'))
    def test_invalid_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ['01.2.3\n', '1.2\n', '1.2.3', '1.2.3\n\n', '1.2.3+local\n']:
                (root/'version.txt').write_text(value)
                with self.assertRaises(ValueError): contract.version(root)
    def test_selection(self):
        self.assertFalse(contract.full_selected('pull_request', {}))
        self.assertFalse(contract.full_selected('push', {}))
        self.assertTrue(contract.full_selected('workflow_dispatch', {}))
        self.assertTrue(contract.full_selected('pull_request', {'pull_request':{'labels':[{'name':'nix-ci'}]}}))
    def needs(self, full='false'):
        return {'policy':{'result':'success','outputs':{'full':full}},
                'nix-eval':{'result':'success'},
                'nix-full':{'result':'success' if full == 'true' else 'skipped'}}
    def test_valid_gates(self):
        contract.gate(self.needs())
        contract.gate(self.needs('true'))
    def test_every_selected_failure_blocks(self):
        for name in ['policy','nix-eval','nix-full']:
            for status in ['failure','cancelled','skipped',None]:
                needs = self.needs('true'); needs[name]['result'] = status
                with self.assertRaises(ValueError): contract.gate(needs)
    def test_unselected_failure_blocks(self):
        needs = self.needs(); needs['nix-full']['result'] = 'failure'
        with self.assertRaises(ValueError): contract.gate(needs)
    def test_missing_dependency_or_selection_blocks(self):
        needs = self.needs(); del needs['nix-full']
        with self.assertRaises(ValueError): contract.gate(needs)
        needs = self.needs(); needs['policy']['outputs'] = {}
        with self.assertRaises(ValueError): contract.gate(needs)

if __name__ == '__main__': unittest.main()
