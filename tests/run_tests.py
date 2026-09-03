#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试入口：跑全部测试套件。

用法:
    python tests/run_tests.py            # 全部测试
    python tests/run_tests.py contracts  # 只跑某个模块
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = {
    'contracts': 'test_contracts',
    'validate': 'test_validate',
    'pipeline': 'test_pipeline',
    'theme': 'test_theme',
    'image': 'test_image',
}


def main():
    args = sys.argv[1:]
    if args:
        names = [MODULES.get(a, a) for a in args]
    else:
        names = list(MODULES.values())
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for n in names:
        suite.addTests(loader.loadTestsFromName(f'tests.{n}'))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
