"""Protect timed media files from being forwarded."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(os.environ.get(
    "AYU_SOURCE_ROOT",
    pathlib.Path(__file__).resolve().parents[2] / "SourceFiles",
))


def function_definition(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


class MediaFileTtlForwarding(unittest.TestCase):
    def test_timed_files_cannot_be_forwarded(self):
        source = (SOURCE / "data/data_media_types.cpp").read_text()
        method = function_definition(
            source,
            "bool MediaFile::allowsForward() const")
        cpp = r'''#include <cstdint>
namespace crl { using time = std::int64_t; }
class MediaFile {
public:
    explicit MediaFile(crl::time ttlSeconds) : _ttlSeconds(ttlSeconds) {}
    crl::time ttlSeconds() const { return _ttlSeconds; }
    bool allowsForward() const;
private:
    crl::time _ttlSeconds = 0;
};
''' + method + r'''
int main() {
    if (!MediaFile(0).allowsForward()) {
        return 1;
    }
    if (MediaFile(30).allowsForward()) {
        return 2;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-media-ttl-forward-") as directory:
            directory = pathlib.Path(directory)
            test_source = directory / "test.cpp"
            binary = directory / "test"
            test_source.write_text(cpp)
            compiler = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run(compiler + [
                "-std=c++20", "-O0", "-Wall", "-Wextra", "-pedantic",
                str(test_source), "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
