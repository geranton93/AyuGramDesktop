"""Keep continuous media slices distinct from serialized cache maps."""
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


def function_source(source, signature):
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


def compile_and_run(cpp, preserve=False):
    preserve = preserve or bool(os.environ.get("AYU_TEST_PERSIST_TEMP_ROOT"))
    if preserve:
        root = pathlib.Path(os.environ["AYU_TEST_PERSIST_TEMP_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        directory = pathlib.Path(tempfile.mkdtemp(
            prefix="ayu-streaming-cache-",
            dir=root,
        ))
        _compile_and_run_in(cpp, directory)
    else:
        with tempfile.TemporaryDirectory(
                prefix="ayu-streaming-cache-") as directory:
            _compile_and_run_in(cpp, pathlib.Path(directory))


def _compile_and_run_in(cpp, directory):
        directory = pathlib.Path(directory)
        test_source = directory / "test.cpp"
        binary = directory / "test"
        test_source.write_text(cpp)
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        subprocess.run(compiler + [
            "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-pedantic",
            str(test_source), "-o", str(binary),
        ], check=True, timeout=60)
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(f"exit {result.returncode}\n{result.stdout}{result.stderr}")


class StreamingCacheParseAmbiguity(unittest.TestCase):
    def test_first_slice_limit_stays_positive_for_large_files(self):
        source = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        slices_count = function_source(
            source,
            "int SlicesCount(uint32 size)",
        )
        max_slice = function_source(
            source,
            "int MaxSliceSize(int sliceNumber, uint32 size)",
        )
        cpp = r'''
#include <algorithm>
#include <cstdint>
#include <stdexcept>
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
constexpr auto kPartSize = uint32(128 * 1024);
constexpr auto kInSlice = uint32(64 * kPartSize);
constexpr auto kMaxOnlyInHeader = 80 * kPartSize;
#define Ensures(condition) \
    do { \
        if (!(condition)) { \
            throw std::runtime_error("ensure"); \
        } \
    } while (false)
''' + slices_count + max_slice + r'''
int main() {
    if (MaxSliceSize(0, uint32(0x80000000U))
        != int(kMaxOnlyInHeader)) return 1;
    if (MaxSliceSize(0, uint32(0xFFFFFFFFU))
        != int(kMaxOnlyInHeader)) return 2;
    if (MaxSliceSize(0, uint32(8192)) != 8192) return 3;
    if (SlicesCount(uint32(510U * kInSlice)) != 510) return 4;
    auto rejectedUnsupportedSize = false;
    try {
        SlicesCount(uint32(0xFFFFFFFFU));
    } catch (const std::runtime_error &) {
        rejectedUnsupportedSize = true;
    }
    if (!rejectedUnsupportedSize) return 5;
    return 0;
}
'''
        compile_and_run(cpp, preserve=True)

    def test_large_file_contiguous_header_and_included_slice_parse_from_cache(self):
        source = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        functions = "\n".join(function_source(source, signature) for signature in (
            "bool IsContiguousSerialization(int serializedSize, int64 maxSliceSize)",
            "bool IsFullInHeader(int64 size)",
            "bool ComputeIsGoodHeader(int64 size, const PartsMap &header)",
            "int SlicesCount(uint32 size)",
            "int MaxSliceSize(int sliceNumber, uint32 size)",
            "bytes::const_span ParseComplexCachedMap(",
            "bytes::const_span ParseCachedMap(",
            "ParsedCacheEntry ParseCacheEntry(",
        ))
        cpp = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <utility>
#include <vector>
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int64 = std::int64_t;
constexpr auto kPartSize = int64(128 * 1024);
constexpr auto kPartsInSlice = 64;
constexpr auto kInSlice = uint32(kPartsInSlice * kPartSize);
constexpr auto kMaxOnlyInHeader = 80 * kPartSize;
constexpr auto kPartsOutsideFirstSliceGood = 8;
class PartsMap {
public:
    using value_type = std::pair<uint32, std::string>;
    using iterator = std::vector<value_type>::iterator;
    using const_iterator = std::vector<value_type>::const_iterator;

    iterator begin() { return _parts.begin(); }
    const_iterator begin() const { return _parts.begin(); }
    iterator end() { return _parts.end(); }
    const_iterator end() const { return _parts.end(); }
    size_t size() const { return _parts.size(); }
    bool empty() const { return _parts.empty(); }

    template <typename... Args>
    void try_emplace(uint32 offset, Args &&...args) {
        const auto i = std::ranges::lower_bound(
            _parts,
            offset,
            std::ranges::less(),
            &value_type::first);
        if (i == _parts.end() || i->first != offset) {
            _parts.emplace(i, offset, std::string(std::forward<Args>(args)...));
        }
    }

    const std::string &at(uint32 offset) const {
        const auto i = std::ranges::lower_bound(
            _parts,
            offset,
            std::ranges::less(),
            &value_type::first);
        if (i == _parts.end() || i->first != offset) {
            throw std::out_of_range("missing part");
        }
        return i->second;
    }

private:
    std::vector<value_type> _parts;
};
struct ParsedCacheEntry {
    PartsMap parts;
    std::optional<PartsMap> included;
};
namespace ranges = std::ranges;
using std::end;
namespace bytes {
using const_span = std::span<const char>;
}
#define Ensures(...) assert(__VA_ARGS__)
template <typename Type>
Type qFromUnaligned(const char *data) {
    auto value = Type();
    std::memcpy(&value, data, sizeof(value));
    return value;
}
''' + functions + r'''
void appendInt(std::string &data, uint32 value) {
    data.append(reinterpret_cast<const char*>(&value), sizeof(value));
}
void appendPartMap(std::string &data, char fill) {
    appendInt(data, 1);
    appendInt(data, 0);
    appendInt(data, uint32(kPartSize));
    data.append(size_t(kPartSize), fill);
}
int main() {
    auto encoded = std::string();
    appendPartMap(encoded, 'h');
    appendPartMap(encoded, 's');
    const auto mappedEntry = ParseCacheEntry(
        bytes::const_span(encoded.data(), encoded.size()),
        0,
        int64(0x80000000U));
    if (mappedEntry.parts.size() != 1) return 4;
    if (mappedEntry.parts.at(0) != std::string(size_t(kPartSize), 'h')) return 5;
    if (!mappedEntry.included || mappedEntry.included->size() != 1) return 6;
    if (mappedEntry.included->at(0) != std::string(size_t(kPartSize), 's')) return 7;

    const auto rawHeader = std::string(size_t(kPartSize), 'r');
    const auto rawEntry = ParseCacheEntry(
        bytes::const_span(rawHeader.data(), rawHeader.size()),
        0,
        int64(0x80000000U));
    if (rawEntry.parts.size() != 1) return 8;
    if (rawEntry.parts.at(0) != rawHeader) return 9;
    if (!rawEntry.included || !rawEntry.included->empty()) return 10;

    auto farHeader = std::string();
    appendInt(farHeader, 1);
    appendInt(farHeader, uint32(0x80000000U));
    appendInt(farHeader, uint32(kPartSize));
    farHeader.append(size_t(kPartSize), 'f');
    const auto farEntry = ParseCacheEntry(
        bytes::const_span(farHeader.data(), farHeader.size()),
        0,
        int64(510U * kInSlice));
    if (farEntry.parts.size() != 1) return 11;
    if (farEntry.parts.at(uint32(0x80000000U))
        != std::string(size_t(kPartSize), 'f')) return 12;

    const auto wideEntry = ParseCacheEntry(
        bytes::const_span(farHeader.data(), farHeader.size()),
        0,
        int64(0x100000000ULL + kInSlice));
    if (wideEntry.parts.size() != 1) return 13;
    if (wideEntry.parts.at(uint32(0x80000000U))
        != std::string(size_t(kPartSize), 'f')) return 14;
    return 0;
}
'''
        compile_and_run(cpp, preserve=True)

    def test_raw_slice_prefix_is_not_treated_as_cache_map(self):
        source = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        contiguous = function_source(
            source,
            "bool IsContiguousSerialization(int serializedSize, int64 maxSliceSize)",
        )
        complex_parser = function_source(
            source,
            "bytes::const_span ParseComplexCachedMap(",
        )
        cache_parser = function_source(
            source,
            "bytes::const_span ParseCachedMap(",
        )
        writer = function_source(
            source,
            "Reader::SerializedSlice Reader::Slices::serializeAndUnloadSlice(",
        )
        self.assertIn(
            "while (IsContiguousSerialization(result.data.size(), maxSize))",
            writer,
        )

        cpp = r'''
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <span>
#include <string>
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int64 = std::int64_t;
constexpr auto kPartSize = int64(128);
constexpr auto kPartsInSlice = 64;
constexpr auto kMaxOnlyInHeader = 80 * kPartSize;
using PartsMap = std::map<uint32, std::string>;
namespace bytes {
using const_span = std::span<const char>;
}
template <typename Type>
Type qFromUnaligned(const char *data) {
    Type value = 0;
    std::memcpy(&value, data, sizeof(value));
    return value;
}
''' + contiguous + complex_parser + cache_parser + r'''
void appendInt(std::string &data, uint32 value) {
    data.append(reinterpret_cast<const char*>(&value), sizeof(value));
}
int main() {
    auto raw = std::string(kPartSize * 8, 'x');
    raw.replace(0, sizeof(uint32), std::string("\1\0\0\0", 4));
    raw.replace(4, sizeof(uint32), std::string("\0\0\0\0", 4));
    const auto partSize = uint32(kPartSize);
    raw.replace(8, sizeof(uint32), std::string(
        reinterpret_cast<const char*>(&partSize), sizeof(partSize)));

    auto parsedRaw = PartsMap();
    const auto rawRemaining = ParseCachedMap(
        parsedRaw,
        bytes::const_span(raw.data(), raw.size()),
        int(raw.size()));
    if (!rawRemaining.empty() || parsedRaw.size() != 8) return 1;
    if (parsedRaw.at(0) != raw.substr(0, kPartSize)) return 2;
    for (auto i = uint32(1); i != 8; ++i) {
        if (parsedRaw.at(i * kPartSize)
            != raw.substr(i * kPartSize, kPartSize)) {
            return 3;
        }
    }

    auto finalRaw = std::string(2 * kPartSize + 12, 'y');
    finalRaw.replace(0, sizeof(uint32), std::string("\1\0\0\0", 4));
    finalRaw.replace(4, sizeof(uint32), std::string("\0\0\0\0", 4));
    finalRaw.replace(8, sizeof(uint32), std::string(
        reinterpret_cast<const char*>(&partSize), sizeof(partSize)));
    auto parsedFinalRaw = PartsMap();
    const auto finalRawRemaining = ParseCachedMap(
        parsedFinalRaw,
        bytes::const_span(finalRaw.data(), finalRaw.size()),
        int(finalRaw.size()));
    if (!finalRawRemaining.empty() || parsedFinalRaw.size() != 3) return 8;
    if (parsedFinalRaw.at(0) != finalRaw.substr(0, kPartSize)
        || parsedFinalRaw.at(kPartSize)
            != finalRaw.substr(kPartSize, kPartSize)
        || parsedFinalRaw.at(2 * kPartSize)
            != finalRaw.substr(2 * kPartSize)) {
        return 9;
    }

    auto ambiguousRaw = std::string();
    appendInt(ambiguousRaw, 1);
    appendInt(ambiguousRaw, 0);
    appendInt(ambiguousRaw, uint32(kPartSize));
    ambiguousRaw.append(kPartSize, 'b');
    auto parsedAmbiguousRaw = PartsMap();
    ParseCachedMap(
        parsedAmbiguousRaw,
        bytes::const_span(ambiguousRaw.data(), ambiguousRaw.size()),
        int(ambiguousRaw.size()));
    if (!parsedAmbiguousRaw.empty()) return 10;

    auto partialRawPrefix = std::string();
    appendInt(partialRawPrefix, 1);
    appendInt(partialRawPrefix, kPartSize);
    appendInt(partialRawPrefix, uint32(kPartSize - 12));
    partialRawPrefix.append(kPartSize - 12, 'p');
    auto parsedPartialRawPrefix = PartsMap();
    ParseCachedMap(
        parsedPartialRawPrefix,
        bytes::const_span(partialRawPrefix.data(), partialRawPrefix.size()),
        int(2 * kPartSize - 12));
    if (!parsedPartialRawPrefix.empty()) return 11;

    auto legacyAlignedLarge = std::string();
    appendInt(legacyAlignedLarge, 10);
    auto legacyAlignedParts = PartsMap();
    for (auto i = uint32(); i != 10; ++i) {
        const auto offset = i * uint32(kPartSize);
        const auto size = (i == 9)
            ? uint32(kPartSize - 84)
            : uint32(kPartSize);
        const auto part = std::string(size, char('a' + i));
        appendInt(legacyAlignedLarge, offset);
        appendInt(legacyAlignedLarge, size);
        legacyAlignedLarge.append(part);
    }
    const auto legacyAlignedMax = int(10 * kPartSize - 84);
    if (legacyAlignedLarge.size() != 10 * kPartSize) return 15;
    ParseCachedMap(
        legacyAlignedParts,
        bytes::const_span(
            legacyAlignedLarge.data(),
            legacyAlignedLarge.size()),
        legacyAlignedMax);
    if (legacyAlignedParts.size() != 10) return 16;
    for (auto i = uint32(); i != 10; ++i) {
        const auto offset = i * uint32(kPartSize);
        const auto size = (i == 9)
            ? uint32(kPartSize - 84)
            : uint32(kPartSize);
        if (legacyAlignedParts.at(offset)
            != std::string(size, char('a' + i))) {
            return 17;
        }
    }

    auto complex = std::string();
    appendInt(complex, 2);
    appendInt(complex, 0);
    appendInt(complex, kPartSize);
    complex.append(kPartSize, 'a');
    appendInt(complex, 2 * kPartSize);
    appendInt(complex, 16);
    complex.append(16, 'z');
    if (IsContiguousSerialization(int(complex.size()), 2 * kPartSize + 16)) {
        return 4;
    }
    auto parsedComplex = PartsMap();
    const auto complexRemaining = ParseCachedMap(
        parsedComplex,
        bytes::const_span(complex.data(), complex.size()),
        2 * kPartSize + 16);
    if (!complexRemaining.empty() || parsedComplex.size() != 2) return 5;
    if (parsedComplex.at(0) != std::string(kPartSize, 'a')) return 6;
    if (parsedComplex.at(2 * kPartSize) != std::string(16, 'z')) return 7;

    auto paddedComplex = std::string();
    appendInt(paddedComplex, 1);
    appendInt(paddedComplex, 0);
    appendInt(paddedComplex, uint32(kPartSize - 12));
    paddedComplex.append(kPartSize - 12, 'c');
    while (IsContiguousSerialization(
            int(paddedComplex.size()),
            int(kPartSize - 12))) {
        paddedComplex.push_back('x');
    }
    auto parsedPaddedComplex = PartsMap();
    const auto paddedRemaining = ParseCachedMap(
        parsedPaddedComplex,
        bytes::const_span(paddedComplex.data(), paddedComplex.size()),
        int(kPartSize - 12));
    if (parsedPaddedComplex.size() != 1
        || parsedPaddedComplex.at(0)
            != std::string(kPartSize - 12, 'c')) {
        return 13;
    }
    if (paddedRemaining.size() != 1 || paddedRemaining[0] != 'x') return 14;
    return 0;
}
'''
        compile_and_run(cpp)


if __name__ == "__main__":
    unittest.main()
