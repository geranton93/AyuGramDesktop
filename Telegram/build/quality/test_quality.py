import importlib.util
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import json
import shutil
from types import SimpleNamespace
from pathlib import Path

spec = importlib.util.spec_from_file_location('quality', Path(__file__).with_name('quality.py'))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class ScopeTests(unittest.TestCase):
    def test_relative_database_file_uses_entry_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'a.cpp'
            source.touch()
            entry = {'directory': str(root), 'file': 'a.cpp', 'arguments': ['clang++', '-c', 'a.cpp']}
            self.assertEqual(quality.select_entries([entry], [str(source)]), [entry])

    def test_missing_translation_unit_is_not_silent_success(self):
        with self.assertRaises(ValueError):
            quality.select_entries([], ['missing.cpp'])

    def test_empty_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            quality.select_entries([], [])

    def test_selected_scope_does_not_include_unrelated_files(self):
        root = Path.cwd()
        entries = [{'directory': str(root), 'file': name} for name in ['a.cpp', 'b.cpp']]
        self.assertEqual(quality.select_entries(entries, [str(root / 'a.cpp')]), entries[:1])

    def test_missing_analyzer_returns_nonzero(self):
        with self.assertRaises(FileNotFoundError):
            quality.require_tool('nonexistent-ayugram-analyzer-123')

    def test_doctor_rejects_broken_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'compile_commands.json'
            database.write_text('[{"directory":"/tmp","file":"a.cpp"}]')
            with patch.object(quality, 'find_tool', return_value='/fake/tool'), \
                    patch.object(quality.subprocess, 'run', return_value=
                                 subprocess.CompletedProcess([], 3, '', 'broken')), \
                    patch('builtins.print'):
                self.assertEqual(quality.doctor(database), 2)

    @unittest.skipUnless(shutil.which('cppcheck'), 'cppcheck not installed')
    def test_cppcheck_rejects_missing_generated_header(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'probe.cpp'
            source.write_text('#include "missing_generated_header.h"\nint main() { return 0; }\n')
            database = Path(directory) / 'compile_commands.json'
            database.write_text(json.dumps([{
                'directory': directory, 'file': str(source),
                'arguments': ['clang++', '-c', str(source)],
            }]))
            result = quality.analyze(SimpleNamespace(
                database=database, file=[str(source)], tool='cppcheck'))
            self.assertNotEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
