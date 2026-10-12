import io
import json
import unittest

from PIL import Image, PngImagePlugin

from yee3.image_metadata import (
    decode_best,
    decode_ifd0_text,
    decode_user_comment,
    decode_xp,
    parse_image_metadata_bytes,
)


class MetadataEncodingTests(unittest.TestCase):
    def test_file_signature_selects_parser_over_extension(self):
        text = "猫の写真\nSteps: 20"
        for fmt, wrong_extension in (("PNG", "jpg"), ("JPEG", "webp"), ("WEBP", "png")):
            with self.subTest(format=fmt):
                if fmt == "PNG":
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text("parameters", text)
                    options = {"pnginfo": metadata}
                else:
                    exif = Image.Exif()
                    exif[0x010E] = text.encode("utf-8") + b"\x00"
                    options = {"exif": exif}
                stream = io.BytesIO()
                Image.new("RGB", (1, 1)).save(stream, format=fmt, **options)
                result = parse_image_metadata_bytes(f"image.{wrong_extension}", stream.getvalue())
                self.assertIsNone(result.error)
                self.assertEqual(result.format, fmt.lower())
                self.assertEqual(result.fields["parameters_raw"], text)

    def test_existing_utf8_and_japanese_encodings(self):
        for text, encoding in (
            ("prompt:cat", "ascii"),
            ("prompt:猫の写真", "utf-8"),
            ("prompt:猫の写真", "shift_jis"),
            ("prompt:①と髙", "ms932"),
            ("猫猫", "shift_jis"),
        ):
            with self.subTest(text=text, encoding=encoding):
                raw = text.encode(encoding)
                self.assertEqual(decode_best(raw), text)
                self.assertEqual(decode_ifd0_text(raw + b"\x00"), text)

    def test_utf16_bom_and_ascii_byte_distribution(self):
        for text in ("prompt:猫の写真", "workflow:{}", "猫の写真\nSteps: 20"):
            for encoding, bom in (("utf-16le", b"\xff\xfe"), ("utf-16be", b"\xfe\xff")):
                for prefix in (b"", bom):
                    with self.subTest(text=text, encoding=encoding, bom=prefix):
                        raw = prefix + text.encode(encoding) + b"\x00\x00"
                        self.assertEqual(decode_best(raw), text)
                        self.assertEqual(decode_ifd0_text(raw), text)
                        self.assertEqual(decode_user_comment(b"UNICODE\x00" + raw), text)

    def test_unicode_comment_without_ascii_defaults_to_piexif_byte_order(self):
        text = "猫の写真"
        self.assertEqual(decode_user_comment(b"UNICODE\x00" + text.encode("utf-16be")), text)
        for encoding, bom in (("utf-16le", b"\xff\xfe"), ("utf-16be", b"\xfe\xff")):
            self.assertEqual(
                decode_user_comment(b"UNICODE\x00" + bom + text.encode(encoding)), text
            )

    def test_unicode_comment_with_sparse_ascii(self):
        for text in (
            "a",
            "猫の写真" * 20 + "\nSteps: 20, Seed: 1",
            "猫の写真" * 600 + "\nSteps: 20, Seed: 1",
            "猫の写真　" * 20 + "\nSteps: 20, Seed: 1",
        ):
            for encoding in ("utf-16le", "utf-16be"):
                with self.subTest(length=len(text), encoding=encoding):
                    raw = b"UNICODE\x00" + text.encode(encoding) + b"\x00\x00"
                    self.assertEqual(decode_user_comment(raw), text)

    def test_existing_explicit_comment_encodings(self):
        self.assertEqual(decode_user_comment(b"ASCII\x00\x00\x00cat\x00"), "cat")
        self.assertEqual(
            decode_user_comment(b"JIS\x00\x00\x00\x00\x00" + "猫の写真".encode("ms932")), "猫の写真"
        )
        self.assertEqual(decode_xp("猫の写真".encode("utf-16le") + b"\x00\x00"), "猫の写真")

    def test_short_ascii_is_not_mistaken_for_utf16(self):
        self.assertEqual(decode_ifd0_text(b"abc\x00"), "abc")
        self.assertEqual(decode_best(b"abc\x00"), "abc\x00")
        self.assertEqual(decode_best(b""), "")
        self.assertEqual(decode_ifd0_text(b"\x00\x00"), "")

    def test_utf8_bom(self):
        text = "猫の写真"
        self.assertEqual(decode_best(text.encode("utf-8-sig")), text)
        self.assertEqual(decode_ifd0_text(text.encode("utf-8-sig") + b"\x00"), text)

    def test_utf16_bom_without_ascii(self):
        text = "猫の写真"
        for encoding, bom in (("utf-16le", b"\xff\xfe"), ("utf-16be", b"\xfe\xff")):
            raw = bom + text.encode(encoding) + b"\x00\x00"
            self.assertEqual(decode_best(raw), text)
            self.assertEqual(decode_ifd0_text(raw), text)

    def test_jpeg_unicode_comment_end_to_end(self):
        for text in ("猫の写真\nSteps: 20, Seed: 1", "猫の写真" * 20 + "\nSteps: 20, Seed: 1"):
            for encoding in ("utf-16le", "utf-16be"):
                with self.subTest(length=len(text), encoding=encoding):
                    exif = Image.Exif()
                    exif[0x8769] = {0x9286: b"UNICODE\x00" + text.encode(encoding)}
                    stream = io.BytesIO()
                    Image.new("RGB", (1, 1)).save(stream, format="JPEG", exif=exif)
                    result = parse_image_metadata_bytes("image.jpg", stream.getvalue())
                    self.assertIsNone(result.error)
                    self.assertEqual(result.fields["parameters_raw"], text)

    def test_comfy_webp_preserves_prompt_and_workflow(self):
        graph = {
            "1": {"class_type": "KSampler", "inputs": {"positive": ["2", 0]}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "猫の写真"}},
        }
        workflow = {"nodes": []}
        exif = Image.Exif()
        exif[0x0110] = "prompt:" + json.dumps(graph)
        exif[0x010F] = "workflow:" + json.dumps(workflow)
        stream = io.BytesIO()
        Image.new("RGB", (1, 1)).save(stream, format="WEBP", exif=exif)
        result = parse_image_metadata_bytes("image.webp", stream.getvalue())
        self.assertIsNone(result.error)
        self.assertEqual(result.fields["prompt_json"], graph)
        self.assertEqual(result.fields["workflow_json"], workflow)
        self.assertEqual(result.fields["prompt"], "猫の写真")

    def test_image_description_end_to_end(self):
        for fmt, extension in (("JPEG", "jpg"), ("WEBP", "webp")):
            for text, encoding in (
                ("cat\nSteps: 20", "ascii"),
                ("猫の写真\nSteps: 20, Seed: 1", "utf-8"),
                ("①と髙\nSteps: 20", "ms932"),
                ("猫の写真\nSteps: 20", "utf-16"),
            ):
                with self.subTest(format=fmt, encoding=encoding):
                    exif = Image.Exif()
                    terminator = b"\x00\x00" if encoding == "utf-16" else b"\x00"
                    exif[0x010E] = text.encode(encoding) + terminator
                    stream = io.BytesIO()
                    Image.new("RGB", (1, 1)).save(stream, format=fmt, exif=exif)
                    result = parse_image_metadata_bytes(f"image.{extension}", stream.getvalue())
                    self.assertIsNone(result.error)
                    self.assertEqual(result.raw["ImageDescription"], text)
                    self.assertEqual(result.fields["parameters_raw"], text)


if __name__ == "__main__":
    unittest.main()
