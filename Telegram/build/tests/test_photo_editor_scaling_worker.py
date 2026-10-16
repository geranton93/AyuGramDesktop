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


class PhotoEditorScalingWorkerTest(unittest.TestCase):
    def test_clipboard_decode_and_scale_use_owned_data_in_worker(self):
        source = (SOURCE / "editor/editor_paint.cpp").read_text()
        handle = block_body(source, "void Paint::handleMimeData(")
        read = block_body(source, "void Paint::readMediaFile(")
        worker = block_body(read, "crl::async(")
        fallback = block_body(
            handle,
            "[imageSource = Core::CopyMimeImageSource(data)]",
        )

        self.assertIn("Core::CopyMimeImageSource(data)", handle)
        self.assertIn("readMediaFile(", handle)
        self.assertIn("return Core::ReadMimeImage(imageSource).image;", fallback)
        self.assertIn("if (!media && imageFallback)", worker)
        self.assertIn("media.image = imageFallback();", worker)
        self.assertNotIn("Core::ReadMimeImage(data)", handle)

    def test_smooth_image_scaling_runs_before_return_to_main_thread(self):
        source = (SOURCE / "editor/editor_paint.cpp").read_text()
        add_image = block_body(source, "void Paint::addImageItem(")
        worker = block_body(add_image, "crl::async(")
        completion = block_body(worker, "crl::on_main(")
        done = block_body(add_image, "const auto done = crl::guard(")

        scale = worker.index("image = image.scaled(")
        return_to_main = worker.index("crl::on_main(")
        self.assertLess(scale, return_to_main)
        self.assertIn("done(std::move(image));", completion)
        self.assertIn("Ui::PixmapFromImage(", done)
        self.assertIn("Qt::KeepAspectRatio", worker)
        self.assertIn("Qt::SmoothTransformation", worker)
        self.assertIn("crl::guard(this", add_image)


if __name__ == "__main__":
    unittest.main()
