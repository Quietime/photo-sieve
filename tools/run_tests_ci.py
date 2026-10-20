"""Run the unit tests like `unittest discover -v`, and report each failure as a GitHub annotation
(CI logs are not always readable from where the failure is investigated; annotations are)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def annotate(kind, test, text):
    tail = text.strip().splitlines()[-25:]
    body = '%0A'.join(line.replace('%', '%25').replace('\r', '') for line in tail)
    print(f'::error title={kind}: {test.id()}::{body}', flush=True)


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')   # same as -s tests
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    for test, text in result.errors:
        annotate('ERROR', test, text)
    for test, text in result.failures:
        annotate('FAIL', test, text)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
