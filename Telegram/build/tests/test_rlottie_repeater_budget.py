"""Keep empty repeater wrappers inside rlottie's item budget."""
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ThirdParty/rlottie/src/lottie/lottieitem.cpp"


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


class RepeaterBudget(unittest.TestCase):
    def test_empty_repeater_wrappers_consume_the_shared_budget(self):
        source = SOURCE.read_text(encoding="utf-8-sig")
        constructor = function_definition(
            source,
            "LOTRepeaterItem::LOTRepeaterItem(LOTRepeaterData *data, int &budget)")
        cpp = r'''#include <algorithm>
#include <cassert>
#include <memory>
#include <vector>
static constexpr int kMaxRepeaterCopies = 1000;
struct LOTGroupData {};
class LOTRepeaterData {
public:
    explicit LOTRepeaterData(int copies) : copies(copies) {}
    LOTGroupData *content() { return &_content; }
    int maxCopies() const { return copies; }
private:
    int copies = 0;
    LOTGroupData _content;
};
class LOTRepeaterItem;
class LOTContentGroupItem {
public:
    LOTContentGroupItem() = default;
    LOTContentGroupItem(LOTGroupData *, int &) {}
    void setParent(LOTRepeaterItem *) {}
};
class LOTRepeaterItem : public LOTContentGroupItem {
public:
    LOTRepeaterItem(LOTRepeaterData *data, int &budget);
    LOTRepeaterData *mRepeaterData = nullptr;
    int mCopies = 0;
    std::vector<std::unique_ptr<LOTContentGroupItem>> mContents;
};
''' + constructor + r'''
int main() {
    auto data = LOTRepeaterData(1000);
    auto budget = 10;
    auto item = LOTRepeaterItem(&data, budget);
    return (budget == 0
        && item.mCopies == 10
        && item.mContents.size() == 10)
        ? 0
        : 1;
}
'''
        with tempfile.TemporaryDirectory(prefix="rlottie-empty-repeater-") as directory:
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
