import ast
import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "Telegram/build/prepare/prepare.py"
DOCKERFILE = ROOT / "Telegram/build/docker/centos_env/Dockerfile"


def recipe_pin(path, pattern):
    matches = re.findall(pattern, path.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise AssertionError(f"expected one dependency pin in {path}, got {matches}")
    return matches[0]


def prepare_function(name, namespace=None):
    tree = ast.parse(PREPARE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {} if namespace is None else namespace
    exec(compile(module, str(PREPARE), "exec"), namespace)
    return namespace[name]


class DependencyRecipePinParity(unittest.TestCase):
    def test_qt612_macos_target_matches_supported_minimum(self):
        minimum = "14.4"
        source_expectations = {
            ROOT / "Telegram/build/prepare/prepare.py": (
                f"'MACOSX_DEPLOYMENT_TARGET': '{minimum}'",
                f"'-mmacosx-version-min={minimum}'",
            ),
            ROOT / "cmake/validate_special_target.cmake": (
                f"CMAKE_OSX_DEPLOYMENT_TARGET {minimum}",
            ),
            ROOT / "Telegram/build/build.sh": (
                f"for macOS {minimum}+",
            ),
            ROOT / "Telegram/build/updates.py": (
                f"OS X {minimum}+",
            ),
            ROOT / "Telegram/build/release.py": (
                f"macOS {minimum}+: Installer",
            ),
        }
        for path, expected_strings in source_expectations.items():
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                for expected in expected_strings:
                    self.assertIn(expected, source)
                self.assertNotIn("10.13", source)

    def test_linux_x11_recipe_pins_use_current_stable_releases(self):
        pins = (
            (r"git clone -b xcb-proto-([0-9]+\.[0-9]+\.[0-9]+)", "1.17.0"),
            (r"git clone -b libxcb-([0-9]+\.[0-9]+\.[0-9]+)", "1.17.0"),
            (r"git clone -b libXext-([0-9]+\.[0-9]+\.[0-9]+)", "1.3.7"),
            (r"git clone -b libXtst-([0-9]+\.[0-9]+\.[0-9]+)", "1.2.5"),
            (r"git clone -b libXfixes-([0-9]+\.[0-9]+\.[0-9]+)", "6.0.2"),
            (r"git clone -b libXv-([0-9]+\.[0-9]+\.[0-9]+)", "1.0.13"),
            (r"git clone -b libXrandr-([0-9]+\.[0-9]+\.[0-9]+)", "1.5.5"),
            (r"git clone -b libXrender-([0-9]+\.[0-9]+\.[0-9]+)", "0.9.12"),
            (r"git clone -b libXdamage-([0-9]+\.[0-9]+\.[0-9]+)", "1.1.7"),
            (r"git clone -b libXcomposite-([0-9]+\.[0-9]+\.[0-9]+)", "0.4.7"),
        )
        for pattern, expected in pins:
            with self.subTest(expected=expected):
                self.assertEqual(expected, recipe_pin(DOCKERFILE, pattern))

    def test_boost_regex_uses_the_same_current_release_in_all_recipes(self):
        self.assertIn(
            "git clone -b boost-1.92.0 https://github.com/boostorg/regex.git",
            PREPARE.read_text(encoding="utf-8"),
        )
        docker = DOCKERFILE.read_text(encoding="utf-8")
        download = docker.index(
            "curl -fsSL --output boost_1_92_0.tar.gz "
            "https://archives.boost.io/release/1.92.0/source/"
            "boost_1_92_0.tar.gz"
        )
        verify = docker.index(
            "echo \"c4a3b310ddd2472416e091067166b0713be97c63f38c212c484ada022fd296ce  "
            "boost_1_92_0.tar.gz\" | sha256sum --check -"
        )
        extract = docker.index("tar -xzf boost_1_92_0.tar.gz", download)
        enter_source = docker.index("cd boost_1_92_0", extract)
        self.assertNotIn("cd boost_*", docker)
        self.assertLess(download, verify)
        self.assertLess(verify, extract)
        self.assertLess(extract, enter_source)
        self.assertIn("rm -rf boost_1_92_0 boost_1_92_0.tar.gz", docker)

    def test_xorg_release_tags_use_the_canonical_freedesktop_repositories(self):
        source = re.sub(
            r"\\\n[ \t]*",
            "",
            DOCKERFILE.read_text(encoding="utf-8"),
        )
        repositories = (
            ("libXext-1.3.7", "libXext", "libxext"),
            ("libXfixes-6.0.2", "libXfixes", "libxfixes"),
            ("libXrandr-1.5.5", "libXrandr", "libxrandr"),
            ("libXdamage-1.1.7", "libXdamage", "libxdamage"),
            ("libXcomposite-0.4.7", "libXcomposite", "libxcomposite"),
        )
        for tag, repository, checkout in repositories:
            with self.subTest(tag=tag):
                self.assertIn(
                    f"git clone -b {tag} --depth=1 "
                    f"https://gitlab.freedesktop.org/xorg/lib/{repository}.git "
                    f"{checkout}",
                    source,
                )

    def test_libxfixes_recipe_installs_compatible_protocol_headers(self):
        source = DOCKERFILE.read_text(encoding="utf-8")
        xorgproto_stage = source.split("FROM builder AS xorgproto\n", 1)[1].split(
            "\nFROM ", 1
        )[0]
        xfixes_stage = source.split("FROM builder AS xfixes\n", 1)[1].split(
            "\nFROM ", 1
        )[0]
        self.assertIn(
            "git clone -b xorgproto-2025.1 --depth=1 "
            "https://gitlab.freedesktop.org/xorg/proto/xorgproto.git",
            xorgproto_stage,
        )
        self.assertIn(
            "meson install -C build --destdir=/usr/src/xorgproto-cache",
            xorgproto_stage,
        )
        self.assertIn(
            "COPY --link --from=xorgproto /usr/src/xorgproto-cache /",
            xfixes_stage,
        )

    def test_xcb_cursor_pin_uses_the_0_1_6_release_commit(self):
        source = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "git remote add origin "
            "https://gitlab.freedesktop.org/xorg/lib/libxcb-cursor.git",
            source,
        )
        self.assertIn(
            "git fetch --depth=1 origin "
            "7b0fa99aa13084a9bf7be4180066f6a74b0adef1",
            source,
        )

    def test_libiconv_recipe_uses_latest_gnu_release(self):
        source = PREPARE.read_text(encoding="utf-8")
        recipe = source.split("stage('libiconv', \"\"\"", 1)[1].split("\"\"\")", 1)[0]
        self.assertEqual(["1.19"], re.findall(r"^\s+VERSION=([0-9]+\.[0-9]+)$", recipe, re.MULTILINE))

    def test_libiconv_archive_is_verified_before_extraction(self):
        source = PREPARE.read_text(encoding="utf-8")
        recipe = source.split("stage('libiconv', \"\"\"", 1)[1].split("\"\"\")", 1)[0]
        checksum = (
            "88dd96a8c0464eca144fc791ae60cd31cd8ee78321e67397e25fc095c4a19aa6"
        )
        verification = (
            f'echo "{checksum}  libiconv.tar.gz" | shasum -a 256 -c -'
        )
        verification_lines = [
            line.strip()
            for line in recipe.splitlines()
            if "shasum -a 256 -c -" in line
        ]
        self.assertEqual([verification], verification_lines)
        self.assertLess(recipe.index("wget --timeout=30"), recipe.index(verification))
        self.assertLess(recipe.index("|| wget -O"), recipe.index(verification))
        self.assertLess(recipe.index(verification), recipe.index("tar -xvzf libiconv.tar.gz"))

    def test_zlib_release_and_minizip_test_flags_match_across_recipes(self):
        release = "da607da739fa6047df13e66a2af6b8bec7c2a498"
        prepare = PREPARE.read_text(encoding="utf-8")
        docker = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(f"git checkout {release}", prepare)
        self.assertIn(f"git fetch --depth=1 origin {release}", docker)
        self.assertIn("-DMINIZIP_BUILD_TESTING=OFF", prepare)
        self.assertIn("-DMINIZIP_BUILD_TESTING=OFF", docker)
        self.assertNotIn("minizipConfig.cmake.in", docker)

    def test_xz_pins_match_across_macos_and_linux_recipes(self):
        pattern = r"git clone -b v([0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/tukaani-project/xz\.git"
        self.assertEqual(
            ("5.8.4", "5.8.4"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_libheif_pins_match_across_macos_and_linux_recipes(self):
        pattern = r"git clone -b v([0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/strukturag/libheif\.git"
        self.assertEqual(
            ("1.23.5", "1.23.5"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_opus_pins_match_across_recipes(self):
        pattern = (
            r"git clone -b (v[0-9]+\.[0-9]+(?:\.[0-9]+)?).*"
            r"https://github\.com/xiph/opus\.git"
        )
        self.assertEqual(
            ("v1.6.1", "v1.6.1"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_dav1d_pins_match_across_recipes(self):
        pattern = r"git clone -b ([0-9]+\.[0-9]+\.[0-9]+).*dav1d\.git"
        self.assertEqual(
            ("1.5.4", "1.5.4"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_breakpad_pins_googletest_v1_18_in_both_recipes(self):
        source = PREPARE.read_text(encoding="utf-8")
        pins = re.findall(
            r"git clone -b ([^ ]+) https://github\.com/google/googletest src/testing",
            source,
        )
        self.assertEqual(["v1.18.0", "v1.18.0"], pins)

    def test_libheif_uses_the_pinned_ffmpeg_patch_and_backend(self):
        patch_pin = "ebcd707f1763651089e3e7220ddd4f97cbcf87e0"
        prepare = PREPARE.read_text(encoding="utf-8")
        docker = DOCKERFILE.read_text(encoding="utf-8")
        patches_recipe = prepare.split("stage('patches',", 1)[1].split(
            "\n\"\"\")", 1
        )[0]
        self.assertIn(f"git checkout {patch_pin}", patches_recipe)
        self.assertIn(f"git fetch --depth=1 origin {patch_pin}", docker)

        ffmpeg_index = prepare.index("stage('ffmpeg',")
        heif_index = prepare.index("stage('libheif',")
        self.assertLess(ffmpeg_index, heif_index)
        heif_recipe = prepare[heif_index:].split("\n\nstage('openal-soft',", 1)[0]
        for setting in (
            "depends:patches/libheif.patch",
            "git apply ../patches/libheif.patch",
            "-DWITH_LIBDE265=ON",
            "-DWITH_FFMPEG_DECODER=ON",
            "-DFFMPEG_ROOT=%LIBS_DIR%/local",
            "-D FFMPEG_ROOT=$USED_PREFIX",
            'SET "PKG_CONFIG_PATH=%LIBS_DIR%/local/lib/pkgconfig"',
            "export PKG_CONFIG_PATH=$USED_PREFIX/lib/pkgconfig",
            "requires=('patches', 'ffmpeg', 'libde265')",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, heif_recipe)

        win_heif = heif_recipe.split("\nwin:\n", 1)[1].split(
            "\nrelease:\n", 1
        )[0]
        mac_heif = heif_recipe.split("\nmac:\n", 1)[1]
        win_pkg_config = win_heif.index(
            'SET "PKG_CONFIG_PATH=%LIBS_DIR%/local/lib/pkgconfig"'
        )
        mac_pkg_config = mac_heif.index(
            "export PKG_CONFIG_PATH=$USED_PREFIX/lib/pkgconfig"
        )
        self.assertLess(win_pkg_config, win_heif.index("    cmake . ^"))
        self.assertLess(mac_pkg_config, mac_heif.index("    cmake . ", mac_pkg_config))

        docker_ffmpeg_index = docker.index("FROM builder AS ffmpeg\n")
        docker_heif_index = docker.index("FROM patches AS heif\n")
        self.assertLess(docker_ffmpeg_index, docker_heif_index)
        heif_stage = docker[docker_heif_index:].split("\nFROM ", 1)[0]
        for setting in (
            "COPY --link --from=ffmpeg /usr/src/ffmpeg-cache /",
            "git apply ../patches/libheif.patch",
            "-DWITH_LIBDE265=ON",
            "-DWITH_FFMPEG_DECODER=ON",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, heif_stage)

    def test_patch_consuming_stages_require_patch_preparation(self):
        source = PREPARE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        consumers = []
        missing = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "stage"
            ):
                continue
            call_source = ast.get_source_segment(source, node)
            if not any(
                reference in call_source
                for reference in ("patches/", r"patches\\")
            ):
                continue
            name = ast.unparse(node.args[0]) if node.args else "<unnamed>"
            consumers.append(name)
            requirements = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "requires"
                ),
                None,
            )
            if requirements is None:
                missing.append(name)
                continue
            try:
                if isinstance(requirements, ast.IfExp):
                    required_stages = (
                        ast.literal_eval(requirements.body),
                        ast.literal_eval(requirements.orelse),
                    )
                else:
                    required_stages = (ast.literal_eval(requirements),)
            except (ValueError, TypeError):
                missing.append(name)
                continue
            if any("patches" not in required for required in required_stages):
                missing.append(name)

        expected = [
            "'dav1d'",
            "'openh264'",
            "'libvpx'",
            "'liblcms2'",
            "'ffmpeg'",
            "'libheif'",
            "'breakpad'",
            "'stackwalk'",
            "'qt_' + qt",
            "'qt_' + qt",
        ]
        self.assertCountEqual(expected, consumers)
        self.assertEqual([], missing, "patch-consuming stages must require patches")

    def test_selected_stage_closure_includes_transitive_prerequisites(self):
        resolve = prepare_function("resolveSelectedStages")
        stages = [
            {"name": "patches", "requires": ()},
            {"name": "ffmpeg", "requires": ("patches",)},
            {"name": "libde265", "requires": ()},
            {
                "name": "libheif",
                "requires": ("patches", "ffmpeg", "libde265"),
            },
            {"name": "other", "requires": ()},
        ]
        self.assertEqual(
            {"patches", "ffmpeg", "libde265", "libheif"},
            resolve({"libheif"}, stages),
        )
        with self.assertRaisesRegex(ValueError, "Unknown required stage"):
            resolve({"libheif"}, [{"name": "libheif", "requires": ("ffmpeg",)}])

    def test_windows_ffmpeg_stage_includes_nv_codec_headers(self):
        source = PREPARE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        ffmpeg_stage = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "stage"
            and node.args
            and ast.literal_eval(node.args[0]) == "ffmpeg"
        )
        requirements = next(
            keyword.value
            for keyword in ffmpeg_stage.keywords
            if keyword.arg == "requires"
        )
        self.assertIsInstance(requirements, ast.IfExp)
        self.assertEqual("win", ast.unparse(requirements.test))
        self.assertEqual(
            (
                "patches",
                "dav1d",
                "openh264",
                "opus",
                "libvpx",
                "nv-codec-headers",
            ),
            ast.literal_eval(requirements.body),
        )
        self.assertEqual(
            ("patches", "dav1d", "openh264", "opus", "libvpx"),
            ast.literal_eval(requirements.orelse),
        )

        resolve = prepare_function("resolveSelectedStages")
        windows_stages = [
            {"name": "patches", "requires": ()},
            {"name": "dav1d", "requires": ("patches",)},
            {"name": "openh264", "requires": ("patches",)},
            {"name": "opus", "requires": ()},
            {"name": "libvpx", "requires": ("patches",)},
            {"name": "nv-codec-headers", "requires": ()},
            {
                "name": "ffmpeg",
                "requires": (
                    "patches",
                    "dav1d",
                    "openh264",
                    "opus",
                    "libvpx",
                    "nv-codec-headers",
                ),
            },
        ]
        mac_stages = [
            {"name": "patches", "requires": ()},
            {"name": "dav1d", "requires": ("patches",)},
            {"name": "openh264", "requires": ("patches",)},
            {"name": "opus", "requires": ()},
            {"name": "libvpx", "requires": ("patches",)},
            {
                "name": "ffmpeg",
                "requires": ("patches", "dav1d", "openh264", "opus", "libvpx"),
            },
        ]
        self.assertEqual(
            {
                "patches",
                "dav1d",
                "openh264",
                "opus",
                "libvpx",
                "nv-codec-headers",
                "ffmpeg",
            },
            resolve({"ffmpeg"}, windows_stages),
        )
        self.assertEqual(
            {"patches", "dav1d", "openh264", "opus", "libvpx", "ffmpeg"},
            resolve({"ffmpeg"}, mac_stages),
        )

    def test_ffmpeg_codec_inputs_are_selected_and_included_in_cache_key(self):
        source = PREPARE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        ffmpeg_stage = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "stage"
            and node.args
            and ast.literal_eval(node.args[0]) == "ffmpeg"
        )
        requirements = next(
            keyword.value
            for keyword in ffmpeg_stage.keywords
            if keyword.arg == "requires"
        )
        self.assertIsInstance(requirements, ast.IfExp)
        windows_requirements = ast.literal_eval(requirements.body)
        other_requirements = ast.literal_eval(requirements.orelse)
        codec_stages = {"dav1d", "openh264", "opus", "libvpx"}
        self.assertTrue(codec_stages.issubset(windows_requirements))
        self.assertTrue(codec_stages.issubset(other_requirements))
        self.assertIn("nv-codec-headers", windows_requirements)
        self.assertNotIn("nv-codec-headers", other_requirements)

        stage_positions = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "stage"
                and node.args
            ):
                continue
            try:
                stage_name = ast.literal_eval(node.args[0])
            except (ValueError, TypeError):
                continue
            if isinstance(stage_name, str):
                stage_positions[stage_name] = node.lineno
        for prerequisite in set(windows_requirements) | set(other_requirements):
            with self.subTest(prerequisite=prerequisite):
                self.assertIn(prerequisite, stage_positions)
                self.assertLess(stage_positions[prerequisite], ffmpeg_stage.lineno)

        resolve = prepare_function("resolveSelectedStages")
        all_stages = [
            {"name": "patches", "requires": ()},
            {"name": "dav1d", "requires": ("patches",)},
            {"name": "openh264", "requires": ("patches",)},
            {"name": "opus", "requires": ()},
            {"name": "libvpx", "requires": ("patches",)},
            {"name": "nv-codec-headers", "requires": ()},
            {"name": "ffmpeg", "requires": windows_requirements},
        ]
        self.assertEqual(
            {"patches", "dav1d", "openh264", "opus", "libvpx", "nv-codec-headers", "ffmpeg"},
            resolve({"ffmpeg"}, all_stages),
        )
        all_stages[-1]["requires"] = other_requirements
        self.assertEqual(
            {"patches", "dav1d", "openh264", "opus", "libvpx", "ffmpeg"},
            resolve({"ffmpeg"}, all_stages),
        )

        cache_stages = [
            {"name": name, "key": name + "-key"}
            for name in ("patches", "dav1d", "openh264", "opus", "libvpx", "nv-codec-headers")
        ]

        def fail(message):
            raise AssertionError(message)

        compute_cache_key = prepare_function(
            "computeCacheKey",
            {
                "hashlib": hashlib,
                "environmentKey": "environment",
                "envForThirdPartyKey": "third-party",
                "stages": cache_stages,
                "error": fail,
            },
        )
        ffmpeg_cache_stage = {
            "location": "Libraries",
            "name": "ffmpeg",
            "version": "9.0.2",
            "commands": "configure ffmpeg",
            "requires": other_requirements,
            "dependencies": [],
        }
        initial_key = compute_cache_key(ffmpeg_cache_stage)
        for codec in sorted(codec_stages):
            with self.subTest(codec=codec):
                codec_stage = next(
                    stage for stage in cache_stages if stage["name"] == codec
                )
                codec_stage["key"] += "-updated"
                self.assertNotEqual(initial_key, compute_cache_key(ffmpeg_cache_stage))
                codec_stage["key"] = codec + "-key"

        windows_cache_stage = dict(
            ffmpeg_cache_stage,
            requires=windows_requirements,
        )
        windows_initial_key = compute_cache_key(windows_cache_stage)
        for prerequisite in windows_requirements:
            with self.subTest(windows_prerequisite=prerequisite):
                prerequisite_stage = next(
                    stage for stage in cache_stages if stage["name"] == prerequisite
                )
                original_key = prerequisite_stage["key"]
                prerequisite_stage["key"] = original_key + "-updated"
                self.assertNotEqual(
                    windows_initial_key,
                    compute_cache_key(windows_cache_stage),
                )
                prerequisite_stage["key"] = original_key

    def test_ada_url_pattern_is_disabled_across_platform_recipes(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        docker = DOCKERFILE.read_text(encoding="utf-8")
        ada_recipe = prepare.split("stage('ada', \"\"\"", 1)[1].split(
            "\"\"\")", 1
        )[0]
        windows_recipe, mac_recipe = ada_recipe.split("mac:", 1)
        windows_configure = windows_recipe.split("cmake -B out .", 1)[1].split(
            "cmake --build out", 1
        )[0]
        mac_configure = mac_recipe.split("cmake -B build .", 1)[1].split(
            "cmake --build build", 1
        )[0]
        docker_ada = docker.split("FROM builder AS ada\n", 1)[1].split(
            "\nFROM ", 1
        )[0]

        self.assertEqual(1, windows_configure.count("ADA_INCLUDE_URL_PATTERN=OFF"))
        self.assertEqual(1, mac_configure.count("ADA_INCLUDE_URL_PATTERN=OFF"))
        self.assertEqual(1, docker_ada.count("-DADA_INCLUDE_URL_PATTERN=OFF"))

    def test_selected_stage_refuses_prerequisites_that_were_skipped(self):
        unmet = prepare_function("unmetRequiredStages")
        stage = {"requires": ("patches", "ffmpeg")}
        self.assertEqual(["ffmpeg"], unmet(stage, {"patches"}))
        self.assertEqual([], unmet(stage, {"patches", "ffmpeg"}))

    def test_libde265_pin_is_built_and_packaged_for_libheif(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        docker = DOCKERFILE.read_text(encoding="utf-8")
        pattern = r"git clone -b (v[0-9.]+).*libde265\.git"
        self.assertEqual(
            ("v1.1.3", "v1.1.3"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )
        prepare_de265 = prepare.split("stage('libde265', \"\"\"", 1)[1].split(
            "\"\"\")", 1
        )[0]
        windows_de265 = prepare_de265.split("win:\n", 1)[1].split(
            "\nrelease:", 1
        )[0]
        release_de265 = prepare_de265.split("release:\n", 1)[1].split(
            "\nmac:", 1
        )[0]
        mac_de265 = prepare_de265.split("mac:\n", 1)[1]
        self.assertIn("-DCMAKE_INSTALL_PREFIX=%LIBS_DIR%/local", prepare_de265)
        self.assertIn("-D CMAKE_INSTALL_PREFIX:STRING=$USED_PREFIX", prepare_de265)
        self.assertIn("-DBUILD_SHARED_LIBS=OFF", prepare_de265)
        self.assertIn("-D BUILD_SHARED_LIBS=OFF", prepare_de265)
        for recipe, build_command, install_command in (
            (
                windows_de265,
                "cmake --build . --config Debug",
                "cmake --install . --config Debug",
            ),
            (
                release_de265,
                "cmake --build . --config Release",
                "cmake --install . --config Release",
            ),
            (
                mac_de265,
                "cmake --build . --config MinSizeRel",
                "cmake --install . --config MinSizeRel",
            ),
        ):
            with self.subTest(build=build_command):
                self.assertIn(build_command, recipe)
                self.assertIn(install_command, recipe)
                self.assertLess(
                    recipe.index(build_command), recipe.index(install_command)
                )

        prepare_heif_stage = prepare.split("stage('libheif', \"\"\"", 1)[1].split(
            "\n\nstage(", 1
        )[0]
        prepare_heif = prepare_heif_stage.split("\"\"\", requires=", 1)[0]
        self.assertIn("-DWITH_LIBDE265=ON", prepare_heif)
        self.assertIn("-DWITH_FFMPEG_DECODER=ON", prepare_heif)
        self.assertIn(
            "requires=('patches', 'ffmpeg', 'libde265')",
            prepare_heif_stage,
        )

        de265_index = docker.index("FROM builder AS de265\n")
        heif_index = docker.index("FROM patches AS heif\n")
        final_index = docker.rindex("\nFROM builder\n")
        de265_stage = docker[de265_index:heif_index]
        heif_stage = docker[heif_index:final_index]
        final_stage = docker[final_index:]
        self.assertIn("-DBUILD_SHARED_LIBS=OFF", de265_stage)
        self.assertIn("cmake --build build", de265_stage)
        self.assertIn(
            "DESTDIR=/usr/src/de265-cache cmake --install build",
            de265_stage,
        )
        self.assertIn(
            "COPY --link --from=de265 /usr/src/de265-cache /",
            heif_stage,
        )
        self.assertLess(
            heif_stage.index("COPY --link --from=de265"),
            heif_stage.index("cmake -B build ."),
        )
        self.assertIn("-DWITH_LIBDE265=ON", heif_stage)
        self.assertIn(
            "COPY --link --from=de265 /usr/src/de265-cache /",
            final_stage,
        )
        self.assertIn(
            "COPY --link --from=heif /usr/src/heif-cache /",
            final_stage,
        )
        self.assertIn(
            "COPY --link --from=ffmpeg /usr/src/ffmpeg-cache /",
            final_stage,
        )

    def test_prepare_selected_stage_builds_prerequisites_and_tracks_their_keys(self):
        source = PREPARE.read_text(encoding="utf-8")
        cache_key = source.split("def computeCacheKey(stage):", 1)[1].split(
            "\ndef keyPath", 1
        )[0]
        self.assertIn("stage['requires']", cache_key)
        self.assertIn("requiredStage['key']", cache_key)

        run_stages = source.split("def runStages():", 1)[1].split(
            "\nif customRunCommand:", 1
        )[0]
        for contract in (
            "requestedStages = set(onlyStages)",
            "resolveSelectedStages(requestedStages, stages)",
            "forcedStages = requestedStages",
            "stage['name'] in forcedStages",
            "completedStages = set()",
            "unmetRequiredStages(stage, completedStages)",
            "completedStages.add(stage['name'])",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, run_stages)
        self.assertEqual(2, run_stages.count("completedStages.add(stage['name'])"))
        self.assertLess(
            run_stages.index("if missingRequiredStages:"),
            run_stages.index("stage['key'] = computeCacheKey(stage)"),
        )

    def test_pipewire_pin_uses_latest_stable_recipe_release(self):
        pattern = r"git clone -b ([0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/PipeWire/pipewire\.git"
        self.assertEqual("1.6.9", recipe_pin(DOCKERFILE, pattern))

    def test_xkbcommon_pin_uses_latest_stable_recipe_release(self):
        pattern = r"git clone -b xkbcommon-([0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/xkbcommon/libxkbcommon\.git"
        self.assertEqual("1.13.2", recipe_pin(DOCKERFILE, pattern))

    def test_ada_v4_pins_match_across_platform_recipes(self):
        pattern = r"git clone -b v([0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/ada-url/ada\.git"
        self.assertEqual(
            ("4.0.0", "4.0.0"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_nv_codec_header_pins_match_across_platform_recipes(self):
        pattern = r"git clone -b n([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+).*https://github\.com/FFmpeg/nv-codec-headers\.git"
        self.assertEqual(
            ("13.1.15.0", "13.1.15.0"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )

    def test_lcms_pins_match_across_macos_and_linux_recipes(self):
        pattern = r"git clone -b (lcms2\.[0-9]+(?:\.[0-9]+)?)"
        self.assertEqual(recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern))

    def test_openssl_pins_match_across_desktop_and_linux_recipes(self):
        pattern = r"git clone -b openssl-([0-9]+\.[0-9]+\.[0-9]+)"
        self.assertEqual(
            ("3.6.5", "3.6.5"),
            (recipe_pin(PREPARE, pattern), recipe_pin(DOCKERFILE, pattern)),
        )


    def test_libvpx_release_pin_and_supported_patch_set_match(self):
        source = PREPARE.read_text(encoding="utf-8")
        recipe = source.split("stage('libvpx', \"\"\"", 1)[1].split("\"\"\")", 1)[0]
        self.assertIn(
            "git checkout 6df3ec34557879fff673706f4a1d9fbd0f3a6f0e",
            recipe,
        )
        self.assertIn("depends:patches/libvpx/000[123]-*.patch", recipe)
        self.assertIn(
            r"for %%i in (..\\patches\\libvpx\\0001-*.patch "
            r"..\\patches\\libvpx\\0002-*.patch "
            r"..\\patches\\libvpx\\0003-*.patch) do git apply %%i",
            recipe,
        )
        self.assertIn(
            "for patch in ../patches/libvpx/000[123]-*.patch; do git apply",
            recipe,
        )
        self.assertIn('SET "TOOLCHAIN=arm64-win64-vs18"', recipe)
        self.assertNotIn("v1.14.1", recipe)
        self.assertNotIn("arm64-win64-vs17-v145", recipe)


    def test_linux_libvpx_matches_desktop_pin_and_patch_set(self):
        source = DOCKERFILE.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^FROM patches AS vpx\n(.*?)(?=^FROM |\Z)",
            source,
        )
        self.assertIsNotNone(match)
        recipe = match.group(1)
        self.assertIn(
            "git fetch --depth=1 origin 6df3ec34557879fff673706f4a1d9fbd0f3a6f0e",
            recipe,
        )
        self.assertIn(
            'for patch in ../patches/libvpx/000[123]-*.patch; '
            'do git apply "$patch"; done',
            recipe,
        )
        self.assertNotIn("12f3a2ac603e8f10742105519e0cd03c3b8f71dd", recipe)
        apply_patches = (
            'for patch in ../patches/libvpx/000[123]-*.patch; '
            'do git apply "$patch"; done'
        )
        self.assertLess(
            recipe.index("git reset --hard FETCH_HEAD"),
            recipe.index(apply_patches),
        )
        self.assertLess(recipe.index(apply_patches), recipe.index("./configure"))

        patch_stage_match = re.search(
            r"(?ms)^FROM builder AS patches\n(.*?)(?=^FROM |\Z)",
            source,
        )
        self.assertIsNotNone(patch_stage_match)
        patch_stage = patch_stage_match.group(1)
        patch_source = PREPARE.read_text(encoding="utf-8")
        patch_recipe = patch_source.split("stage('patches', \"\"\"", 1)[1].split(
            "\"\"\")", 1
        )[0]
        docker_patch_pin = re.search(
            r"git fetch --depth=1 origin ([0-9a-f]{40})",
            patch_stage,
        )
        prepare_patch_pin = re.search(
            r"git checkout ([0-9a-f]{40})",
            patch_recipe,
        )
        self.assertIsNotNone(docker_patch_pin)
        self.assertIsNotNone(prepare_patch_pin)
        self.assertEqual(
            "ebcd707f1763651089e3e7220ddd4f97cbcf87e0",
            docker_patch_pin.group(1),
        )
        self.assertEqual(docker_patch_pin.group(1), prepare_patch_pin.group(1))

    def test_qt6_pins_match_across_builds_and_preserve_qt5(self):
        qt_source = (ROOT / "Telegram/build/qt_version.py").read_text(
            encoding="utf-8",
        )
        qt6_pins = re.findall(
            r"os\.environ\['QT'\]\s*=\s*'(6\.[^']+)'",
            qt_source,
        )
        qt5_pins = re.findall(
            r"os\.environ\['QT'\]\s*=\s*'(5\.[^']+)'",
            qt_source,
        )
        docker_pins = re.findall(
            r"(?m)^QT=([0-9]+(?:\.[0-9]+){2})$",
            DOCKERFILE.read_text(encoding="utf-8"),
        )

        self.assertEqual(2, len(qt6_pins))
        self.assertEqual(("6.12.0", "6.12.0"), tuple(qt6_pins))
        self.assertEqual((qt6_pins[0],), tuple(docker_pins))
        self.assertEqual(("5.15.19",), tuple(qt5_pins))


if __name__ == "__main__":
    unittest.main()
