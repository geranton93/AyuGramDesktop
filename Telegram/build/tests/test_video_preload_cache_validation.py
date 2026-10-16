"""Validate cached video coverage before stories mark it preloaded."""
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


class VideoPreloadCacheValidation(unittest.TestCase):
    def test_partial_video_cache_is_not_marked_preloaded_or_removed(self):
        preload = (SOURCE / "data/data_media_preload.cpp").read_text()
        check = function_definition(preload, "void VideoPreload::check()")
        self.assertIn("if (complete)", check)
        self.assertIn("else if (empty)", check)
        self.assertNotIn("cacheBigFile().remove(", check)
        self.assertIn("self->load();", check)
        self.assertIn("self->callDone(false);", check)
        self.assertLess(
            check.index("if (complete)"),
            check.index("else if (empty)"),
        )
        self.assertLess(
            check.index("else if (empty)"),
            check.index("self->callDone(false);"),
        )

    def test_reader_complex_cache_integer_load_handles_unaligned_data(self):
        reader = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        parser = function_definition(reader, "bytes::const_span ParseComplexCachedMap(")
        self.assertIn("#include <QtCore/QtEndian>", reader)
        self.assertIn("qFromUnaligned<uint32>(data.data())", parser)
        self.assertNotIn("reinterpret_cast<const uint32*>", parser)

    def test_reader_rejects_ambiguous_legacy_cache_and_keeps_unambiguous_maps(self):
        reader = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        contiguous = function_definition(
            reader,
            "bool IsContiguousSerialization(",
        )
        complex_parser = function_definition(
            reader,
            "bytes::const_span ParseComplexCachedMap(",
        )
        cache_parser = function_definition(
            reader,
            "bytes::const_span ParseCachedMap(",
        )
        cpp = r'''#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <span>
#include <string>
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int64 = int;
auto kPartSize = 1024;
constexpr auto kPartsInSlice = 64;
auto kMaxOnlyInHeader = 80 * kPartSize;
using PartsMap = std::map<uint32, std::string>;
namespace bytes { using const_span = std::span<const char>; }
template <typename T>
T qFromUnaligned(const char *data) {
    auto result = T();
    std::memcpy(&result, data, sizeof(result));
    return result;
}
''' + contiguous + complex_parser + cache_parser + r'''void appendInt(std::string &data, uint32 value) {
    data.append(reinterpret_cast<const char*>(&value), sizeof(value));
}
void appendPart(std::string &data, uint32 offset, uint32 size, char fill) {
    appendInt(data, offset);
    appendInt(data, size);
    data.append(size, fill);
}
int main() {
    auto serialized = std::string();
    appendInt(serialized, 2);
    appendPart(serialized, 0, 1024, 'a');
    appendPart(serialized, 2048, 1004, 'c');
    if (serialized.size() != 2048) return 1;

    auto parts = PartsMap();
    const auto remaining = ParseCachedMap(
        parts,
        bytes::const_span(serialized.data(), serialized.size()),
        3052);
    if (!IsContiguousSerialization(int(serialized.size()), 3052)) return 20;
    if (!remaining.empty() || !parts.empty()) return 2;

    auto raw = std::string(2048, 'x');
    auto rawParts = PartsMap();
    const auto rawRemaining = ParseCachedMap(
        rawParts,
        bytes::const_span(raw.data(), raw.size()),
        3052);
    if (!rawRemaining.empty()) return 7;
    if (rawParts.size() != 2
        || rawParts.find(0) == rawParts.end()
        || rawParts.find(1024) == rawParts.end()
        || rawParts.find(2048) != rawParts.end()) return 8;

    auto mp4 = std::string(2048, char(0));
    mp4[3] = char(24);
    mp4.replace(4, 4, "ftyp");
    auto mp4Parts = PartsMap();
    ParseCachedMap(mp4Parts, bytes::const_span(mp4.data(), mp4.size()), 3052);
    if (mp4Parts.size() != 2
        || mp4Parts.find(0)->second != mp4.substr(0, 1024)) return 13;

    auto legacyAlignedPrefix = std::string();
    appendInt(legacyAlignedPrefix, 2);
    appendPart(legacyAlignedPrefix, 0, 1024, 'a');
    appendPart(legacyAlignedPrefix, 1024, 1004, 'b');
    if (legacyAlignedPrefix.size() != 2 * 1024) return 15;
    auto legacyPrefixParts = PartsMap();
    ParseCachedMap(
        legacyPrefixParts,
        bytes::const_span(
            legacyAlignedPrefix.data(),
            legacyAlignedPrefix.size()),
        2028);
    if (legacyPrefixParts.size() != 2
        || legacyPrefixParts.find(0) == legacyPrefixParts.end()
        || legacyPrefixParts.find(1024) == legacyPrefixParts.end()) return 16;
    auto oversizedRaw = std::string(2048, 'r');
    auto oversizedRawParts = PartsMap();
    ParseCachedMap(
        oversizedRawParts,
        bytes::const_span(oversizedRaw.data(), oversizedRaw.size()),
        2028);
    if (!oversizedRawParts.empty()) return 17;

    auto webm = std::string(2048, char(0));
    webm[0] = char(0x1A);
    webm[1] = char(0x45);
    webm[2] = char(0xDF);
    webm[3] = char(0xA3);
    auto webmParts = PartsMap();
    ParseCachedMap(
        webmParts,
        bytes::const_span(webm.data(), webm.size()),
        3052);
    if (webmParts.size() != 2
        || webmParts.find(0)->second != webm.substr(0, 1024)) return 14;

    auto legacyHeaderAndSlice = std::string();
    appendInt(legacyHeaderAndSlice, 2);
    appendPart(legacyHeaderAndSlice, 0, 1024, 'h');
    appendPart(legacyHeaderAndSlice, 81920, 984, 'H');
    appendInt(legacyHeaderAndSlice, 2);
    appendPart(legacyHeaderAndSlice, 0, 1024, 'a');
    appendPart(legacyHeaderAndSlice, 1024, 1024, 'b');
    if (legacyHeaderAndSlice.size() != std::size_t(4 * kPartSize)) return 10;
    auto headerParts = PartsMap();
    const auto includedData = ParseCachedMap(
        headerParts,
		bytes::const_span(
			legacyHeaderAndSlice.data(),
			legacyHeaderAndSlice.size()),
		82904,
		true);
    if (headerParts.size() != 2 || includedData.size() != 2068) return 11;
    auto includedParts = PartsMap();
    const auto includedRemaining = ParseCachedMap(
        includedParts,
        includedData,
        64 * kPartSize);
    if (!includedRemaining.empty()
        || includedParts.size() != 2
        || includedParts.find(0) == includedParts.end()
        || includedParts.find(1024) == includedParts.end()) return 12;

    auto legacyHeaderAndFullSlice = std::string();
    appendInt(legacyHeaderAndFullSlice, 1);
    appendPart(
        legacyHeaderAndFullSlice,
        80 * kPartSize,
        kPartSize - 528,
        'H');
    appendInt(legacyHeaderAndFullSlice, 64);
    for (auto i = uint32(); i != 64; ++i) {
        appendPart(
            legacyHeaderAndFullSlice,
            i * kPartSize,
            kPartSize,
            char('a' + (i % 26)));
    }
    const auto largeHeaderFileSize = 81 * kPartSize - 528;
    if (legacyHeaderAndFullSlice.size()
        != std::size_t(65 * kPartSize)) {
        return 21;
    }
    if (!IsContiguousSerialization(
            int(legacyHeaderAndFullSlice.size()),
            largeHeaderFileSize)) {
        return 22;
    }
    auto largeHeaderParts = PartsMap();
    const auto largeIncludedData = ParseCachedMap(
        largeHeaderParts,
        bytes::const_span(
            legacyHeaderAndFullSlice.data(),
            legacyHeaderAndFullSlice.size()),
        largeHeaderFileSize,
        true);
    if (largeHeaderParts.size() != 1
        || largeHeaderParts.find(80 * kPartSize) == largeHeaderParts.end()
        || largeHeaderParts.at(80 * kPartSize).size()
            != std::size_t(kPartSize - 528)) {
        return 23;
    }
    if (largeIncludedData.size() != std::size_t(64 * kPartSize + 516)) {
        return 24;
    }
    auto largeIncludedParts = PartsMap();
    const auto largeIncludedRemaining = ParseCachedMap(
        largeIncludedParts,
        largeIncludedData,
        64 * kPartSize);
    if (!largeIncludedRemaining.empty()
        || largeIncludedParts.size() != 64
        || largeIncludedParts.find(63 * kPartSize) == largeIncludedParts.end()) {
        return 25;
    }

    auto partial = std::string();
    appendInt(partial, 3);
    appendPart(partial, 0, 1024, 'p');
    appendPart(partial, 1024, 1024, 'q');
    appendInt(partial, 2048);
    appendInt(partial, 1024);
    partial.append(10, 'x');
    auto partialParts = PartsMap();
    ParseCachedMap(
        partialParts,
        bytes::const_span(partial.data(), partial.size()),
        3052);
    if (partialParts.size() != 2
        || partialParts.find(0) == partialParts.end()
        || partialParts.find(1024) == partialParts.end()
        || partialParts.find(2048) != partialParts.end()) return 9;

    kPartSize = 131072;
    kMaxOnlyInHeader = 80 * kPartSize;
    auto productionLegacyPrefix = std::string();
    appendInt(productionLegacyPrefix, 1);
    appendPart(productionLegacyPrefix, 0, 131060, 'p');
    if (productionLegacyPrefix.size() != 131072) return 18;
    auto productionPrefixParts = PartsMap();
    ParseCachedMap(
        productionPrefixParts,
        bytes::const_span(
            productionLegacyPrefix.data(),
            productionLegacyPrefix.size()),
        131060);
    if (productionPrefixParts.size() != 1
        || productionPrefixParts.find(0) == productionPrefixParts.end()
        || productionPrefixParts.find(0)->second.size() != 131060) return 19;
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-legacy-reader-cache-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reader_parse_cache_entry_restores_and_publishes_included_slice(self):
        reader = (SOURCE / "media/streaming/media_streaming_reader.cpp").read_text()
        read_from_cache = function_definition(
            reader,
            "void Reader::readFromCache(int sliceNumber)",
        )
        self.assertIn("auto entry = ParseCacheEntry(", read_from_cache)
        self.assertIn(
            "strong->results.emplace(sliceNumber, std::move(entry.parts));",
            read_from_cache,
        )
        self.assertIn("if (!sliceNumber && entry.included)", read_from_cache)
        self.assertIn(
            "strong->results.emplace(1, std::move(*entry.included));",
            read_from_cache,
        )

        signatures = (
            "bool IsContiguousSerialization(",
            "bool IsFullInHeader(",
            "bool ComputeIsGoodHeader(",
            "int SlicesCount(",
            "int MaxSliceSize(",
            "bytes::const_span ParseComplexCachedMap(",
            "bytes::const_span ParseCachedMap(",
            "ParsedCacheEntry ParseCacheEntry(",
        )
        functions = "\n".join(
            function_definition(reader, signature)
            for signature in signatures
        )
        cpp = r'''#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>
using std::begin;
using std::end;
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int64 = std::int64_t;
constexpr auto kPartSize = int64(1024);
constexpr auto kPartsInSlice = 64;
constexpr auto kInSlice = uint32(kPartsInSlice * kPartSize);
constexpr auto kMaxOnlyInHeader = 80 * kPartSize;
constexpr auto kPartsOutsideFirstSliceGood = 8;
#define Ensures(...) assert(__VA_ARGS__)
namespace ranges {
struct less {
    template <typename Left, typename Right>
    bool operator()(const Left &left, const Right &right) const {
        return left < right;
    }
};
template <typename Range, typename Value, typename Compare, typename Projection>
auto lower_bound(
        const Range &range,
        const Value &value,
        Compare compare,
        Projection projection) {
    return std::lower_bound(
        range.begin(),
        range.end(),
        value,
        [&](const auto &entry, const auto &target) {
            return compare(std::invoke(projection, entry), target);
        });
}
}
namespace bytes { using const_span = std::span<const char>; }
struct PartsMap : std::vector<std::pair<uint32, std::string>> {
    using Base = std::vector<std::pair<uint32, std::string>>;
    using Base::Base;

    auto try_emplace(uint32 offset, const char *data, std::size_t size) {
        const auto i = std::lower_bound(
            begin(),
            end(),
            offset,
            [](const auto &entry, uint32 value) {
                return entry.first < value;
            });
        if (i != end() && i->first == offset) {
            return std::pair(i, false);
        }
        return std::pair(
            insert(i, { offset, std::string(data, size) }),
            true);
    }

    auto try_emplace(uint32 offset, std::string value) {
        const auto i = std::lower_bound(
            begin(),
            end(),
            offset,
            [](const auto &entry, uint32 value) {
                return entry.first < value;
            });
        if (i != end() && i->first == offset) {
            return std::pair(i, false);
        }
        return std::pair(
            insert(i, { offset, std::move(value) }),
            true);
    }

    auto find(uint32 offset) {
        return std::find_if(begin(), end(), [&](const auto &entry) {
            return entry.first == offset;
        });
    }

    auto find(uint32 offset) const {
        return std::find_if(begin(), end(), [&](const auto &entry) {
            return entry.first == offset;
        });
    }
};
struct ParsedCacheEntry {
    PartsMap parts;
    std::optional<PartsMap> included;
};
template <typename T>
T qFromUnaligned(const char *data) {
    auto result = T();
    std::memcpy(&result, data, sizeof(result));
    return result;
}
''' + functions + r'''
void appendInt(std::string &data, uint32 value) {
    data.append(reinterpret_cast<const char*>(&value), sizeof(value));
}
void appendPart(std::string &data, uint32 offset, uint32 size, char fill) {
    appendInt(data, offset);
    appendInt(data, size);
    data.append(size, fill);
}
int main() {
    auto serialized = std::string();
    appendInt(serialized, 2);
    appendPart(serialized, 0, 1024, 'h');
    appendPart(serialized, 80 * kPartSize, kPartSize - 40, 'H');
    appendInt(serialized, 2);
    appendPart(serialized, 0, 1024, 'a');
    appendPart(serialized, 1024, 1024, 'b');
    if (serialized.size() != 4 * kPartSize) return 1;

    auto entry = ParseCacheEntry(
        bytes::const_span(serialized.data(), serialized.size()),
        0,
        81 * kPartSize - 40);
    if (entry.parts.size() != 2 || !entry.included) return 2;
    if (entry.parts.find(80 * kPartSize) == entry.parts.end()) return 3;
    if (entry.included->size() != 2
        || entry.included->find(0) == entry.included->end()
        || entry.included->find(kPartSize) == entry.included->end()) {
        return 4;
    }

    auto results = std::map<int, PartsMap>();
    results.emplace(0, std::move(entry.parts));
    if (!0 && entry.included) {
        results.emplace(1, std::move(*entry.included));
    }
    if (results.size() != 2
        || results.find(0)->second.find(80 * kPartSize)
            == results.find(0)->second.end()
        || results.find(1)->second.find(kPartSize)
            == results.find(1)->second.end()) {
        return 5;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-reader-cache-entry-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_story_preload_confirms_persisted_cache_before_success(self):
        source = (SOURCE / "data/data_media_preload.cpp").read_text()
        done = function_definition(source, "void VideoPreload::done(QByteArray result)")
        self.assertIn("putIfEmpty(", done)
        self.assertIn("cacheBigFile().get(", done)
        self.assertIn("HasContiguousVideoPreloadPrefix(", done)
        self.assertIn("callDone(complete)", done)
        self.assertNotIn("callDone(success)", done)

    def test_reader_cache_formats_require_contiguous_prefix_coverage(self):
        path = SOURCE / "data/data_media_preload.cpp"
        source = path.read_text()
        expected_part_size = function_definition(
            source,
            "bool HasExpectedVideoPreloadPartSize(",
        )
        validator = function_definition(
            source,
            "bool HasContiguousVideoPreloadPrefix(",
        )
        check = function_definition(source, "void VideoPreload::check()")
        self.assertIn("HasContiguousVideoPreloadPrefix(", check)
        self.assertIn("callDone(true)", check)
        self.assertIn("callDone(false)", check)
        self.assertNotIn("cacheBigFile().remove(", check)

        cpp = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <string>
#include <tuple>
using int64 = std::int64_t;
using uint64 = std::uint64_t;
using uint32 = std::uint32_t;
namespace Storage { inline constexpr int64 kDownloadPartSize = 1024; }
''' + expected_part_size + validator + r'''
void appendInt(std::string &data, uint32 value) {
    data.append(reinterpret_cast<const char*>(&value), sizeof(value));
}
void appendPart(std::string &data, uint32 offset, uint32 size, char fill) {
    appendInt(data, offset);
    appendInt(data, size);
    data.append(size, fill);
}
void appendMap(
        std::string &data,
        std::initializer_list<std::tuple<uint32, uint32, char>> parts) {
    appendInt(data, static_cast<uint32>(parts.size()));
    for (const auto &[offset, size, fill] : parts) {
        appendPart(data, offset, size, fill);
    }
}
std::string complex(std::initializer_list<std::tuple<uint32, uint32, char>> parts) {
    auto data = std::string();
    appendMap(data, parts);
    if (data.size() % 1024 == 0) {
        data.push_back('\0');
    }
    return data;
}
int main() {
    constexpr auto part = int64(1024);
    constexpr auto full = int64(6000);
    constexpr auto prefix = int64(2048);

    constexpr auto largeFile = int64(100 * 1024);
    auto zeroCountWithIncludedSlice = std::string();
    appendInt(zeroCountWithIncludedSlice, 0);
    appendMap(zeroCountWithIncludedSlice, {
        {0, 1024, 'a'},
        {1024, 1024, 'b'},
        {2048, 1024, 'c'},
    });
    if (!HasContiguousVideoPreloadPrefix(
            zeroCountWithIncludedSlice.data(),
            zeroCountWithIncludedSlice.size(),
            largeFile,
            3 * part,
            part)) {
        return 17;
    }

    auto oversizedCountWithIncludedSlice = std::string();
    appendInt(oversizedCountWithIncludedSlice, 81);
    appendMap(oversizedCountWithIncludedSlice, {
        {0, 1024, 'a'},
        {1024, 1024, 'b'},
        {2048, 1024, 'c'},
    });
    if (!HasContiguousVideoPreloadPrefix(
            oversizedCountWithIncludedSlice.data(),
            oversizedCountWithIncludedSlice.size(),
            largeFile,
            3 * part,
            part)) {
        return 18;
    }

    auto legacyAlignedGap = std::string();
    appendMap(legacyAlignedGap, {{0, 1024, 'a'}, {2048, 1004, 'c'}});
    if (legacyAlignedGap.size() != 2 * part) return 19;
    if (HasContiguousVideoPreloadPrefix(
            legacyAlignedGap.data(),
            legacyAlignedGap.size(),
            3052,
            2 * part,
            part)) {
        return 20;
    }

    auto legacyAlignedPrefix = std::string();
    appendMap(legacyAlignedPrefix, {{0, 1024, 'a'}, {1024, 1004, 'b'}});
    if (legacyAlignedPrefix.size() != 2 * part) return 21;
    if (!HasContiguousVideoPreloadPrefix(
            legacyAlignedPrefix.data(),
            legacyAlignedPrefix.size(),
            2028,
            2028,
            part)) {
        return 22;
    }

    auto validComplex = complex({{0, 1024, 'a'}, {1024, 1024, 'b'}, {2048, 1024, 'c'}});
    if (!HasContiguousVideoPreloadPrefix(
            validComplex.data(), validComplex.size(), full, prefix, part)) {
        return 1;
    }

    auto partialPrimary = std::string();
    appendInt(partialPrimary, 3);
    appendPart(partialPrimary, 0, 1024, 'a');
    appendPart(partialPrimary, 1024, 1024, 'b');
    appendInt(partialPrimary, 2048);
    appendInt(partialPrimary, 1024);
    partialPrimary.append(10, 'x');
    if (!HasContiguousVideoPreloadPrefix(
            partialPrimary.data(),
            partialPrimary.size(),
            full,
            prefix,
            part)) {
        return 15;
    }

    auto extraHeader = complex({{0, 1024, 'a'}, {1024, 1024, 'b'}, {2048, 1024, 'c'}, {4096, 1024, 'h'}});
    if (!HasContiguousVideoPreloadPrefix(
            extraHeader.data(), extraHeader.size(), full, prefix, part)) {
        return 2;
    }

    auto headerAndSlice = std::string();
    appendMap(headerAndSlice, {{0, 1024, 'h'}, {64 * 1024, 1024, 'H'}});
    appendMap(headerAndSlice, {{1024, 1024, 'a'}, {2048, 1024, 'b'}, {3072, 1024, 'c'}});
    while (headerAndSlice.size() % part == 0
        || int64(headerAndSlice.size()) == largeFile) {
        headerAndSlice.push_back('\0');
    }
    if (!HasContiguousVideoPreloadPrefix(
            headerAndSlice.data(),
            headerAndSlice.size(),
            largeFile,
            4 * part,
            part)) {
        return 13;
    }

    auto legacyHeaderAndSlice = std::string();
    appendMap(legacyHeaderAndSlice, {{0, 1024, 'h'}, {81920, 984, 'H'}});
    appendMap(legacyHeaderAndSlice, {{0, 1024, 'a'}, {1024, 1024, 'b'}});
    if (legacyHeaderAndSlice.size() != 4 * part) return 23;
    if (!HasContiguousVideoPreloadPrefix(
            legacyHeaderAndSlice.data(),
            legacyHeaderAndSlice.size(),
            82904,
            2 * part,
            part)) {
        return 24;
    }

    auto partialIncludedSlice = std::string();
    appendMap(partialIncludedSlice, {{0, 1024, 'h'}, {64 * 1024, 1024, 'H'}});
    appendInt(partialIncludedSlice, 4);
    appendPart(partialIncludedSlice, 1024, 1024, 'a');
    appendPart(partialIncludedSlice, 2048, 1024, 'b');
    appendPart(partialIncludedSlice, 3072, 1024, 'c');
    appendInt(partialIncludedSlice, 4096);
    appendInt(partialIncludedSlice, 1024);
    partialIncludedSlice.append(10, 'x');
    if (!HasContiguousVideoPreloadPrefix(
            partialIncludedSlice.data(),
            partialIncludedSlice.size(),
            largeFile,
            4 * part,
            part)) {
        return 16;
    }

    constexpr auto shortHeaderFile = int64(80 * 1024);
    auto ignoredIncludedSlice = std::string();
    appendMap(ignoredIncludedSlice, {{0, 1024, 'h'}});
    appendMap(ignoredIncludedSlice, {{1024, 1024, 'a'}, {2048, 1024, 'b'}, {3072, 1024, 'c'}});
    if (HasContiguousVideoPreloadPrefix(
            ignoredIncludedSlice.data(),
            ignoredIncludedSlice.size(),
            shortHeaderFile,
            4 * part,
            part)) {
        return 14;
    }

    auto gap = complex({{0, 1024, 'a'}, {3072, 1024, 'c'}});
    if (HasContiguousVideoPreloadPrefix(
            gap.data(), gap.size(), full, prefix, part)) {
        return 3;
    }

    auto shortPrefix = complex({{0, 1024, 'a'}});
    if (HasContiguousVideoPreloadPrefix(
            shortPrefix.data(), shortPrefix.size(), full, prefix, part)) {
        return 4;
    }

    auto badCount = std::string("\0\0\0\0", 4);
    if (HasContiguousVideoPreloadPrefix(
            badCount.data(), badCount.size(), full, prefix, part)) {
        return 5;
    }

    auto truncatedCount = std::string("\1\0", 2);
    if (HasContiguousVideoPreloadPrefix(
            truncatedCount.data(), truncatedCount.size(), full, prefix, part)) {
        return 6;
    }

    auto truncatedPart = std::string();
    appendInt(truncatedPart, 1);
    appendInt(truncatedPart, 0);
    appendInt(truncatedPart, 1024);
    truncatedPart.append(10, 'x');
    if (HasContiguousVideoPreloadPrefix(
            truncatedPart.data(), truncatedPart.size(), full, prefix, part)) {
        return 7;
    }

    auto unaligned = complex({{0, 1024, 'a'}, {1023, 1024, 'x'}});
    if (HasContiguousVideoPreloadPrefix(
            unaligned.data(), unaligned.size(), full, prefix, part)) {
        return 8;
    }

    auto contiguous = std::string(2048, 'c');
    if (!HasContiguousVideoPreloadPrefix(
            contiguous.data(), contiguous.size(), full, prefix, part)) {
        return 9;
    }
    auto mp4 = std::string(2048, char(0));
    mp4[3] = char(24);
    mp4.replace(4, 4, "ftyp");
    if (!HasContiguousVideoPreloadPrefix(
            mp4.data(), mp4.size(), full, prefix, part)) {
        return 25;
    }

    auto webm = std::string(2048, char(0));
    webm[0] = char(0x1A);
    webm[1] = char(0x45);
    webm[2] = char(0xDF);
    webm[3] = char(0xA3);
    if (!HasContiguousVideoPreloadPrefix(
            webm.data(), webm.size(), full, prefix, part)) {
        return 26;
    }
    if (HasContiguousVideoPreloadPrefix(
            contiguous.data(), contiguous.size(), full, prefix + 1, part)) {
        return 10;
    }

    auto shortFinalFile = std::string(1500, 'f');
    if (!HasContiguousVideoPreloadPrefix(
            shortFinalFile.data(), shortFinalFile.size(), 1500, 1500, part)) {
        return 11;
    }
    if (HasContiguousVideoPreloadPrefix(
            nullptr, 0, full, prefix, part)) {
        return 12;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="ayu-video-cache-validation-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
