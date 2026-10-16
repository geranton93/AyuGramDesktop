import pathlib
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[2] / "SourceFiles"


def block_body(source, signature):
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
    return source[opening + 1:cursor - 1]


class PhotoEditorMimeFallbackTest(unittest.TestCase):
    def test_unreadable_local_url_uses_copied_mime_image_in_worker(self):
        source = (SOURCE / "editor/editor_paint.cpp").read_text()
        handle = block_body(source, "void Paint::handleMimeData(")
        read = block_body(source, "void Paint::readMediaFile(")
        fallback = block_body(
            handle,
            "[imageSource = Core::CopyMimeImageSource(data)]",
        )
        worker = block_body(read, "crl::async([=]")
        mime = (SOURCE / "core/mime_type.cpp").read_text()
        copy_image_source = block_body(
            mime,
            "MimeImageSource CopyMimeImageSource(",
        )
        read_image_source = block_body(
            mime,
            "MimeImageData ReadMimeImage(MimeImageSource source)",
        )

        self.assertIn("Core::CopyMimeImageSource(data)", handle)
        self.assertIn(
            "return Core::ReadMimeImage(imageSource).image;",
            fallback,
        )
        self.assertIn("Storage::ReadPhotoEditorMedia(", worker)
        self.assertIn("if (!media && imageFallback)", worker)
        self.assertIn("media.image = imageFallback();", worker)
        self.assertIn("qvariant_cast<QImage>(data->imageData())",
                      copy_image_source)
        self.assertIn("source.image", read_image_source)
        self.assertNotIn("qvariant_cast<QImage>", read_image_source)

    def test_successful_file_or_video_result_precedes_mime_fallback(self):
        source = (SOURCE / "editor/editor_paint.cpp").read_text()
        read = block_body(source, "void Paint::readMediaFile(")
        worker = block_body(read, "crl::async([=]")

        file_read = worker.index("auto media = Storage::ReadPhotoEditorMedia(")
        fallback_check = worker.index("if (!media && imageFallback)")
        fallback_apply = worker.index("media.image = imageFallback();")
        publish = worker.index("crl::on_main(")

        self.assertLess(file_read, fallback_check)
        self.assertLess(fallback_check, fallback_apply)
        self.assertLess(fallback_apply, publish)

    def test_jpeg_preference_is_copied_and_decoded_from_owned_bytes(self):
        mime = (SOURCE / "core/mime_type.cpp").read_text()
        copy_image_source = block_body(
            mime,
            "MimeImageSource CopyMimeImageSource(",
        )
        read_image_source = block_body(
            mime,
            "MimeImageData ReadMimeImage(MimeImageSource source)",
        )
        shared_api = block_body(
            mime,
            "MimeImageData ReadMimeImage(not_null<const QMimeData*> data)",
        )

        self.assertIn('data->hasFormat(u"application/x-td-use-jpeg"_q)',
                      copy_image_source)
        self.assertIn('data->data(u"image/jpeg"_q)', copy_image_source)
        self.assertIn("if (source.preferContent)", read_image_source)
        self.assertIn("Images::Read({ .content = bytes })", read_image_source)
        self.assertIn("Images::kReadMaxArea", read_image_source)
        self.assertNotIn("data->", read_image_source)
        self.assertIn("CopyMimeImageSource(data)", shared_api)


if __name__ == "__main__":
    unittest.main()
