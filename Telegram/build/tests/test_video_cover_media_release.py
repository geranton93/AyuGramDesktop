"""Guard video cover media release with its owning heavy part."""
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


class VideoCoverMediaRelease(unittest.TestCase):
    def test_failed_cover_cache_is_remembered_and_retried_for_new_data(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        document_header = (SOURCE / "data/data_document.h").read_text()
        document_source = (SOURCE / "data/data_document.cpp").read_text()
        getter = function_definition(
            media_source,
            "Image *DocumentMedia::goodThumbnail() const",
        )
        setter = function_definition(
            media_source,
            "void DocumentMedia::setGoodThumbnail(QImage thumbnail)",
        )
        set_bytes = function_definition(
            media_source,
            "void DocumentMedia::setBytes(const QByteArray &bytes)",
        )
        collect_local_data = function_definition(
            media_source,
            "void DocumentMedia::collectLocalData(not_null<DocumentMedia*> local)",
        )
        set_location = function_definition(
            document_source,
            "void DocumentData::setLocation(const Core::FileLocation &loc)",
        )
        finish_load = function_definition(
            document_source,
            "void DocumentData::finishLoad()",
        )
        reader = function_definition(
            media_source,
            "void DocumentMedia::ReadOrGenerateThumbnail(",
        )
        generate = function_definition(
            media_source,
            "void DocumentMedia::GenerateGoodThumbnail(",
        )
        invalidate = function_definition(
            document_source,
            "void DocumentData::invalidateGoodThumbnail(bool removeCache)",
        )

        self.assertIn("Failed = 0x08", document_header)
        self.assertIn("!_owner->goodThumbnailFailed()", getter)
        self.assertIn("value == \"(failed)\"", reader)
        self.assertIn("document->setGoodThumbnailFailed(true)", reader)
        self.assertIn("image.isNull()", reader)
        self.assertNotIn('QByteArray("(failed)")', reader)
        self.assertIn("result.isNull()", generate)
        self.assertIn("document->goodThumbnailGeneration() != generation", reader)
        self.assertIn("document->goodThumbnailGeneration() != generation", generate)
        self.assertIn("_owner->setGoodThumbnailFailed(false);", setter)
        self.assertIn("_bytes != bytes", set_bytes)
        self.assertIn("_owner->goodThumbnailFailed()", set_bytes)
        self.assertIn("_owner->invalidateGoodThumbnail(removeCache);", set_bytes)
        self.assertIn("_owner->goodThumbnailGenerating()", set_bytes)
        self.assertIn("_owner->goodThumbnailGenerating()", collect_local_data)
        self.assertIn("_owner->invalidateGoodThumbnail(removeCache);", collect_local_data)
        self.assertIn("++_goodThumbnailGeneration;", invalidate)
        self.assertIn("GoodThumbnailFlag::Failed", invalidate)
        self.assertIn("if (removeCache)", invalidate)
        self.assertIn("cache().remove(goodThumbnailCacheKey())", invalidate)

        self.assertIn("const auto changed = (_location != loc);", set_location)
        self.assertIn("if (changed && retry", set_location)
        self.assertIn("goodThumbnailGenerating()", set_location)
        self.assertIn("invalidateGoodThumbnail(removeCache);", set_location)
        self.assertIn("media->goodThumbnailSourceChanged();", set_location)
        self.assertLess(
            finish_load.index("setLocation(Core::FileLocation("),
            finish_load.index("setGoodThumbnailDataReady();"),
        )
        self.assertIn("media->setBytes(_loader->bytes());", finish_load)
        self.assertIn(
            "const auto thumbnailGeneration = goodThumbnailGeneration();",
            finish_load,
        )
        self.assertIn("goodThumbnailGeneration() == thumbnailGeneration", finish_load)
        self.assertIn("invalidateGoodThumbnail(false);", finish_load)
        self.assertIn("setGoodThumbnailRetryFailedCache(true);", finish_load)
        self.assertNotIn("removeFailedThumbnail", finish_load)

    def test_source_change_clears_stale_view_and_path_cache_entries(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        document_source = (SOURCE / "data/data_document.cpp").read_text()
        source_changed = function_definition(
            media_source,
            "void DocumentMedia::goodThumbnailSourceChanged()",
        )
        loaded_in_cache = function_definition(
            document_source,
            "void DocumentData::setLoadedInMediaCacheLocation()",
        )

        self.assertIn("_goodThumbnail.reset();", source_changed)
        self.assertIn("ReadOrGenerateThumbnail(_owner);", source_changed)
        self.assertIn("const auto changed = !_location.isEmpty();", loaded_in_cache)
        self.assertIn("goodThumbnailGenerating()", loaded_in_cache)
        self.assertIn("goodThumbnailFailed()", loaded_in_cache)
        self.assertIn("invalidateGoodThumbnail(removeCache);", loaded_in_cache)

    def test_fresh_source_retries_persisted_failure_without_discarding_image_cache(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        document_header = (SOURCE / "data/data_document.h").read_text()
        document_source = (SOURCE / "data/data_document.cpp").read_text()
        set_bytes = function_definition(
            media_source,
            "void DocumentMedia::setBytes(const QByteArray &bytes)",
        )
        collect_local_data = function_definition(
            media_source,
            "void DocumentMedia::collectLocalData(not_null<DocumentMedia*> local)",
        )
        set_location = function_definition(
            document_source,
            "void DocumentData::setLocation(const Core::FileLocation &loc)",
        )
        finish_load = function_definition(
            document_source,
            "void DocumentData::finishLoad()",
        )
        reader = function_definition(
            media_source,
            "void DocumentMedia::ReadOrGenerateThumbnail(",
        )
        failure_fingerprint = function_definition(
            media_source,
            "QByteArray GoodThumbnailFailureCacheValue(",
        )
        generate = function_definition(
            media_source,
            "void DocumentMedia::GenerateGoodThumbnail(",
        )

        self.assertIn("RetryFailedCache = 0x10", document_header)
        self.assertIn("goodThumbnailRetryFailedCache() const", document_header)
        self.assertIn("setGoodThumbnailRetryFailedCache(bool retry)", document_header)
        self.assertIn("else if (unchecked && !_goodThumbnail)", set_bytes)
        self.assertIn("setGoodThumbnailRetryFailedCache(true)", set_bytes)
        self.assertIn("else if (unchecked && !_goodThumbnail)", collect_local_data)
        self.assertIn("setGoodThumbnailRetryFailedCache(true)", collect_local_data)
        self.assertIn("else if (changed", set_location)
        self.assertIn("setGoodThumbnailRetryFailedCache(true)", set_location)
        self.assertIn("uncheckedGoodThumbnail", finish_load)
        self.assertIn("setGoodThumbnailRetryFailedCache(true)", finish_load)
        self.assertLess(
            reader.index("document->goodThumbnailRetryFailedCache()"),
            reader.index("document->setGoodThumbnailFailed(true)"),
        )
        self.assertIn("GenerateGoodThumbnail(", reader)
        self.assertIn("document->owner().cache().remove(", reader)
        self.assertIn('value.startsWith("(failed):")', reader)
        self.assertIn("value != currentCacheValue", reader)
        self.assertIn("invalidateGoodThumbnail(false);", finish_load)
        self.assertIn("setGoodThumbnailRetryFailedCache(true);", finish_load)
        self.assertIn('"(failed):bytes:"', failure_fingerprint)
        self.assertIn('"(failed):file:"', failure_fingerprint)
        self.assertIn("QCryptographicHash::hash(", failure_fingerprint)
        self.assertIn("QCryptographicHash::Sha256", failure_fingerprint)
        self.assertIn("data,", failure_fingerprint)
        self.assertIn("QFileInfo(filepath)", failure_fingerprint)
        self.assertIn("info.isFile()", failure_fingerprint)
        self.assertIn("info.isReadable()", failure_fingerprint)
        self.assertIn("info.size()", failure_fingerprint)
        self.assertIn("info.lastModified().toMSecsSinceEpoch()", failure_fingerprint)
        self.assertIn("info.metadataChangeTime().toMSecsSinceEpoch()", failure_fingerprint)
        self.assertIn("info.birthTime().toMSecsSinceEpoch()", failure_fingerprint)
        self.assertNotIn("file.read(", failure_fingerprint)
        self.assertNotIn("hash.addData(chunk)", failure_fingerprint)
        self.assertIn("filepath", failure_fingerprint)
        self.assertIn("return QByteArray();", failure_fingerprint)
        self.assertNotIn('return QByteArray("(failed)");', failure_fingerprint)
        self.assertNotIn("kFailureFingerprintSampleSize", failure_fingerprint)
        self.assertIn("GoodThumbnailFailureCacheValue(", generate)
        self.assertIn("const auto failureCacheValue = data.isEmpty()", generate)
        self.assertLess(
            generate.index("const auto failureCacheValue = data.isEmpty()"),
            generate.index("auto result = PrepareGoodThumbnail(filepath, data, type);"),
        )
        self.assertIn("cache = failureCacheValue.isEmpty()", generate)
        self.assertIn("location.get()", generate)
        self.assertIn("filepath", generate)
        self.assertIn("buffer.open(QIODevice::WriteOnly)", generate)
        self.assertIn("result.save(&buffer, format, kGoodThumbQuality)", generate)
        self.assertIn("&& !bytes.isEmpty()", generate)
        self.assertLess(
            generate.index("buffer.open(QIODevice::WriteOnly)"),
            generate.index("result.save(&buffer, format, kGoodThumbQuality)"),
        )
        cache_logic_start = generate.index("auto cache = QByteArray();")
        cache_logic_end = generate.index("if (locationAccessEnabled)", cache_logic_start)
        cache_logic = generate[cache_logic_start:cache_logic_end]
        failure_guard = cache_logic.index("if (result.isNull()) {")
        failure_value = cache_logic.index("cache = failureCacheValue.isEmpty()")
        fallback_value = cache_logic.index("? GoodThumbnailFailureCacheValue(")
        encoded_branch = cache_logic.index("} else if (encoded) {")
        self.assertLess(failure_guard, failure_value)
        self.assertLess(failure_value, fallback_value)
        self.assertLess(fallback_value, encoded_branch)
        self.assertIn("if (!cache.isEmpty())", generate)

    def test_failure_fingerprint_comparison_runs_off_main_thread(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        reader = function_definition(
            media_source,
            "void DocumentMedia::ReadOrGenerateThumbnail(",
        )
        failure_branch_start = reader.index(
            'if (value == "(failed)" ||'
        )
        failure_branch_end = reader.index(
            "} else if (value.isEmpty())",
            failure_branch_start,
        )
        failure_branch = reader[failure_branch_start:failure_branch_end]

        self.assertLess(
            failure_branch.index("crl::async([=, location = std::move(location)]"),
            failure_branch.index("GoodThumbnailFailureCacheValue("),
        )
        self.assertLess(
            failure_branch.index("GoodThumbnailFailureCacheValue("),
            failure_branch.rindex("crl::on_main("),
        )
        self.assertIn("location->accessEnable()", failure_branch)
        self.assertIn("location->accessDisable()", failure_branch)

    def test_legacy_failure_is_migrated_when_source_is_available(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        reader = function_definition(
            media_source,
            "void DocumentMedia::ReadOrGenerateThumbnail(",
        )
        failure_branch_start = reader.index(
            'if (value == "(failed)" ||'
        )
        failure_branch_end = reader.index(
            "} else if (value.isEmpty())",
            failure_branch_start,
        )
        failure_branch = reader[failure_branch_start:failure_branch_end]

        self.assertIn("const auto hasSource = !sourceData.isEmpty()", failure_branch)
        self.assertIn("retryLegacyFailure || hasSource", failure_branch)
        self.assertIn("hasSource && value != currentCacheValue", failure_branch)

    def test_invalid_cached_image_is_discarded_and_regenerated(self):
        media_source = (SOURCE / "data/data_document_media.cpp").read_text()
        reader = function_definition(
            media_source,
            "void DocumentMedia::ReadOrGenerateThumbnail(",
        )
        invalid_image_start = reader.index("if (image.isNull())")
        invalid_image_end = reader.index(
            "if (const auto active = document->activeMediaView())",
            invalid_image_start,
        )
        invalid_image_branch = reader[invalid_image_start:invalid_image_end]

        self.assertIn("document->owner().cache().remove(", invalid_image_branch)
        self.assertIn("GenerateGoodThumbnail(", invalid_image_branch)
        self.assertLess(
            invalid_image_branch.index("cache().remove("),
            invalid_image_branch.index("GenerateGoodThumbnail("),
        )
        self.assertIn(
            "document->goodThumbnailGeneration() != generation",
            reader,
        )
        self.assertIn("generation);", invalid_image_branch)
        self.assertNotIn("setGoodThumbnailFailed(true)", invalid_image_branch)

    def test_gif_destruction_keeps_cover_alive_until_session_cleanup(self):
        source = (SOURCE / "history/view/media/history_view_gif.cpp").read_text()
        destructor = function_definition(source, "Gif::~Gif()")
        keep_alive = destructor.index(
            "_data->owner().keepAlive(base::take(_videoCoverMedia));")
        stop_story_polling = destructor.index("togglePollingStory(false);")
        self.assertLess(keep_alive, stop_story_polling)

    def test_history_gif_releases_cover_when_heavy_part_is_unloaded(self):
        source = (SOURCE / "history/view/media/history_view_gif.cpp").read_text()
        unload = function_definition(source, "void Gif::unloadHeavyPart()")
        data_media = unload.index("_dataMedia = nullptr;")
        cover_media = unload.index("_videoCoverMedia = nullptr;")
        self.assertLess(data_media, cover_media)

    def test_overview_video_releases_cover_with_heavy_part(self):
        source = (SOURCE / "overview/overview_layout.cpp").read_text()
        clear = function_definition(source, "void Video::clearHeavyPart()")
        data_media = clear.index("_dataMedia = nullptr;")
        cover_media = clear.index("_videoCoverMedia = nullptr;")
        self.assertLess(data_media, cover_media)


if __name__ == "__main__":
    unittest.main()
