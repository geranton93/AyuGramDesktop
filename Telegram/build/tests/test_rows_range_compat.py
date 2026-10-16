"""Compile the Ayu compatibility row-range helper against clipped geometry."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


class RowsRangeCompatibility(unittest.TestCase):
    def test_clamps_visible_rows_for_edges_and_empty_ranges(self):
        cpp = r'''
#include "ayu/rows_range.h"
#include <cstdio>

int main() {
    auto checks = 0;
    auto failures = 0;
    const auto check = [&](const char *name, Ayu::RowsRange actual, int from, int till) {
        ++checks;
        if (actual.from != from || actual.till != till) {
            ++failures;
            std::fprintf(stderr, "%s: got [%d, %d), expected [%d, %d)\n",
                name, actual.from, actual.till, from, till);
        }
    };
    check("single row", Ayu::RowsInRange(10, 20, 10, 5), 1, 2);
    check("partial row", Ayu::RowsInRange(9, 10, 10, 5), 0, 1);
    check("negative clip start", Ayu::RowsInRange(-8, 2, 10, 5), 0, 1);
    check("past list end", Ayu::RowsInRange(50, 70, 10, 5), 5, 5);
    check("empty list", Ayu::RowsInRange(0, 10, 10, 0), 0, 0);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-rows-range-") as directory:
            directory = pathlib.Path(directory)
            source = directory / "test.cpp"
            binary = directory / "test"
            source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
                "-I", str(SOURCE), str(source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
