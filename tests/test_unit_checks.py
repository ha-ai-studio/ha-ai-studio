import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unit_checks', ROOT / 'scripts/unit_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class UnitGroupsTests(unittest.TestCase):
    def test_groups_cover_every_module_exactly_once(self):
        directory = ROOT / 'tests'
        all_modules = checks.selected_modules(directory, 'all')
        grouped = [path for group in checks.GROUPS
                   for path in checks.selected_modules(directory, group)]
        self.assertCountEqual(grouped, all_modules)
        for group in checks.GROUPS:
            self.assertTrue(checks.selected_modules(directory, group))

    def test_new_modules_default_to_core(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test_future_feature.py'
            path.touch()
            self.assertEqual(checks.selected_modules(directory, 'core'), [path])
            self.assertEqual(checks.selected_modules(directory, 'plugins'), [])

    def test_unknown_group_fails(self):
        with self.assertRaises(ValueError):
            checks.selected_modules(ROOT / 'tests', 'typo')

    def test_runner_propagates_failures_and_keeps_groups_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'tests').mkdir()
            runner = root / 'scripts/unit_checks.py'
            shutil.copyfile(ROOT / 'scripts/unit_checks.py', runner)
            (root / 'tests/test_plugins.py').write_text(
                'import unittest\nclass Example(unittest.TestCase):\n'
                ' def test_failure(self): self.fail("synthetic failure")\n')
            (root / 'tests/test_future.py').write_text(
                'import unittest\nclass Example(unittest.TestCase):\n'
                ' def test_success(self): pass\n')
            for group, expected in [('all', 1), ('plugins', 1), ('core', 0),
                                    ('local', 2), ('typo', 2)]:
                with self.subTest(group=group):
                    result = subprocess.run([sys.executable, str(runner), '--group', group],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
