"""Run all Python tests or one exhaustive, non-overlapping CI group."""
import argparse
from pathlib import Path
import sys
import unittest


GROUP_MODULES = {
    'plugins': {'test_plugins.py'},
    'releases': {'test_plugins_release.py', 'test_repository_versions.py'},
    'local': {'test_local_plugins.py', 'test_plugin_device_templates.py'},
}
GROUPS = ('core', *GROUP_MODULES)


def selected_modules(directory, group):
    if group not in ('all', *GROUPS):
        raise ValueError(f'Unknown test group: {group}')
    modules = sorted(Path(directory).glob('test_*.py'))
    assigned = set().union(*GROUP_MODULES.values())
    return [path for path in modules if group == 'all' or
            (path.name not in assigned if group == 'core' else
             path.name in GROUP_MODULES[group])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=('all', *GROUPS), default='all')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / 'tests'
    modules = selected_modules(directory, args.group)
    if not modules:
        parser.error('Selected test group is empty')
    # Discover one module at a time, retaining unittest import/error semantics.
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for path in modules:
        suite.addTests(loader.discover(str(directory), pattern=path.name))
    print(f'Python test group: {args.group} ({suite.countTestCases()} tests)', flush=True)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
