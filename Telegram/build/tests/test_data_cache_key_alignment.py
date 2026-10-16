"""Guard cache-key reads from unaligned SHA digest slices."""
import os
import pathlib
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


class DataCacheKeyAlignment(unittest.TestCase):
    def test_web_document_digest_parts_use_unaligned_reads(self):
        path = SOURCE / "data/data_types.cpp"
        source = path.read_text()
        function = function_definition(
            source,
            "Storage::Cache::Key WebDocumentCacheKey(")

        self.assertIn("qFromUnaligned<uint32>(bytes1.data())", function)
        self.assertIn("qFromUnaligned<uint64>(bytes2.data())", function)
        self.assertNotIn("reinterpret_cast", function)

    def test_url_digest_parts_use_unaligned_reads(self):
        path = SOURCE / "data/data_types.cpp"
        source = path.read_text()
        function = function_definition(source, "Storage::Cache::Key UrlCacheKey(")

        self.assertIn("qFromUnaligned<uint32>(bytes1.data())", function)
        self.assertIn("qFromUnaligned<uint64>(bytes2.data())", function)
        self.assertIn("qFromUnaligned<uint16>(bytes3.data())", function)
        self.assertNotIn("reinterpret_cast", function)


if __name__ == "__main__":
    unittest.main()
