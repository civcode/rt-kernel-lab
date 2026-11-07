#!/usr/bin/env python3
"""Fail the build if Kconfig silently discards a requested fragment setting."""
import pathlib
import re
import sys

PATTERN = re.compile(r'^(CONFIG_[A-Za-z0-9_]+)=(.*)$')
NO = re.compile(r'^# (CONFIG_[A-Za-z0-9_]+) is not set$')


def values(path):
    result = {}
    for line in pathlib.Path(path).read_text().splitlines():
        match = PATTERN.fullmatch(line) or NO.fullmatch(line)
        if match:
            result[match.group(1)] = match.group(2) if match.re is PATTERN else 'n'
    return result


def check(actual_file, fragments):
    actual = values(actual_file)
    expected = {}
    for fragment in fragments:
        expected.update(values(fragment))
    failures = {key: (value, actual.get(key)) for key, value in expected.items() if actual.get(key) != value}
    if failures:
        for key, (want, got) in sorted(failures.items()):
            print(f'CONFIG MISMATCH {key}: requested={want}, resolved={got}', file=sys.stderr)
        return False
    return True


if __name__ == '__main__':
    sys.exit(0 if check(sys.argv[1], sys.argv[2:]) else 1)
