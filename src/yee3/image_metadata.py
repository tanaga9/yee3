import html
import json
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
A1111_KEYS = ("Steps:", "Sampler:", "CFG scale:", "Seed:", "Size:", "Model:")
EXIF_TEXT_TAGS = {
    0x010E: "ImageDescription",
    0x9286: "UserComment",
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
}


@dataclass
class ImageMetadataResult:
    path: str
    format: str = "unknown"
    fields: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: str | None = None


def parse_image_metadata(path: str) -> ImageMetadataResult:
    normalized_path = unicodedata.normalize("NFD", path)
    try:
        with open(normalized_path, "rb") as f:
            data = f.read()
    except Exception as exc:
        return ImageMetadataResult(path=normalized_path, error=str(exc))
    return parse_image_metadata_bytes(normalized_path, data)


def parse_image_metadata_bytes(path: str, data: bytes) -> ImageMetadataResult:
    normalized_path = unicodedata.normalize("NFD", path)
    result = ImageMetadataResult(path=normalized_path)

    fmt = detect_format(data, Path(normalized_path).suffix.lower())
    result.format = fmt

    try:
        if fmt == "png":
            result.raw = parse_png(data)
        elif fmt == "jpeg":
            result.raw = parse_jpeg(data)
        elif fmt == "webp":
            result.raw = parse_webp(data)
        result.fields = normalize_fields(result.raw)
    except Exception as exc:
        result.error = str(exc)

    return result


def detect_format(data: bytes, ext: str) -> str:
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return "jpeg"
    if ext in {".jpg", ".jpeg"}:
        return "jpeg"
    if ext == ".png":
        return "png"
    if ext == ".webp":
        return "webp"
    return "unknown"


def parse_png(data: bytes) -> dict[str, str]:
    raw: dict[str, str] = {}
    if not data.startswith(PNG_SIGNATURE):
        return raw

    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        chunk_type = data[offset : offset + 4]
        offset += 4
        chunk_data = data[offset : offset + length]
        offset += length + 4

        if chunk_type == b"tEXt":
            key, text = parse_png_text_chunk(chunk_data)
            if key:
                raw[key] = text
        elif chunk_type == b"zTXt":
            key, text = parse_png_ztxt_chunk(chunk_data)
            if key:
                raw[key] = text
        elif chunk_type == b"iTXt":
            key, text = parse_png_itxt_chunk(chunk_data)
            if key:
                raw[key] = text
        elif chunk_type == b"IEND":
            break

    return raw


def parse_png_text_chunk(data: bytes) -> tuple[str, str]:
    if b"\x00" not in data:
        return "", ""
    key, text = data.split(b"\x00", 1)
    return key.decode("latin-1", errors="ignore"), text.decode(
        "latin-1", errors="ignore"
    )


def parse_png_ztxt_chunk(data: bytes) -> tuple[str, str]:
    if b"\x00" not in data:
        return "", ""
    key, rest = data.split(b"\x00", 1)
    if not rest or rest[0] != 0:
        return key.decode("latin-1", errors="ignore"), ""
    try:
        text = zlib.decompress(rest[1:]).decode("latin-1", errors="ignore")
    except Exception:
        text = ""
    return key.decode("latin-1", errors="ignore"), text


def parse_png_itxt_chunk(data: bytes) -> tuple[str, str]:
    if b"\x00" not in data:
        return "", ""
    key_end = data.index(b"\x00")
    key = data[:key_end].decode("latin-1", errors="ignore")
    pos = key_end + 1
    if pos + 2 > len(data):
        return key, ""
    compression_flag = data[pos]
    pos += 2

    lang_end = data.find(b"\x00", pos)
    if lang_end == -1:
        return key, ""
    pos = lang_end + 1

    translated_end = data.find(b"\x00", pos)
    if translated_end == -1:
        return key, ""
    pos = translated_end + 1

    payload = data[pos:]
    try:
        if compression_flag:
            text = zlib.decompress(payload).decode("utf-8", errors="ignore")
        else:
            text = payload.decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    return key, text


def parse_jpeg(data: bytes) -> dict[str, str]:
    raw: dict[str, str] = {}
    if len(data) < 2 or data[:2] != b"\xff\xd8":
        return raw

    offset = 2
    xmp_parts: list[str] = []
    comments: list[str] = []
    exif_sources: list[tuple[str, str]] = []

    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD9, 0xDA):
            break
        if 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        seg_length = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        if seg_length < 2 or offset + seg_length - 2 > len(data):
            break
        payload = data[offset : offset + seg_length - 2]
        offset += seg_length - 2

        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            exif_sources.extend(extract_exif_texts(payload[6:]))
        elif marker == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0\x00"):
            xmp_parts.append(decode_best(payload.split(b"\x00", 1)[1]))
        elif marker == 0xFE:
            comment = decode_best(payload)
            if comment:
                comments.append(comment)

    apply_exif_sources(raw, exif_sources)
    if xmp_parts:
        xmp_text = "".join(xmp_parts)
        raw["XMP"] = xmp_text
        apply_xmp_sources(raw, xmp_text)
    if comments:
        raw["Comment"] = "\n".join(comments)
    return raw


def parse_webp(data: bytes) -> dict[str, str]:
    raw: dict[str, str] = {}
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return raw

    xmp_parts: list[str] = []
    exif_sources: list[tuple[str, str]] = []
    offset = 12

    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        start = offset + 8
        end = start + chunk_size
        if end > len(data):
            break
        payload = data[start:end]
        offset = end + (chunk_size & 1)

        if chunk_id == b"EXIF":
            exif_sources.extend(extract_exif_texts(payload))
        elif chunk_id == b"XMP ":
            xmp_parts.append(decode_best(payload))

    apply_exif_sources(raw, exif_sources)
    if xmp_parts:
        xmp_text = "".join(xmp_parts)
        raw["XMP"] = xmp_text
        apply_xmp_sources(raw, xmp_text)
    return raw


def apply_exif_sources(raw: dict[str, str], sources: list[tuple[str, str]]):
    priority = ["UserComment", "XPComment", "ImageDescription", "XPTitle"]
    preferred = sort_sources_by_priority(sources, priority)
    for key, value in preferred:
        if key not in raw and value:
            raw[key] = value
    raw_parameters = choose_first_text([value for _, value in preferred])
    if raw_parameters:
        raw["parameters"] = raw_parameters


def apply_xmp_sources(raw: dict[str, str], xmp_text: str):
    attributes = extract_xmp_attributes(xmp_text)
    for key, value in attributes.items():
        if value and key not in raw:
            raw[key] = value
    if "parameters" not in raw:
        candidate = (
            attributes.get("parameters")
            or attributes.get("Parameters")
            or attributes.get("sd-metadata")
            or attributes.get("sd_metadata")
        )
        if candidate is None and xmp_text.strip():
            candidate = xmp_text
        if candidate:
            raw["parameters"] = candidate


def sort_sources_by_priority(
    sources: list[tuple[str, str]], priority: list[str]
) -> list[tuple[str, str]]:
    rank = {name: index for index, name in enumerate(priority)}
    return sorted(sources, key=lambda item: rank.get(item[0], len(priority)))


def extract_exif_texts(exif_data: bytes) -> list[tuple[str, str]]:
    if len(exif_data) < 8:
        return []
    endian_bytes = exif_data[:2]
    if endian_bytes == b"II":
        endian = "<"
    elif endian_bytes == b"MM":
        endian = ">"
    else:
        return []
    if read_uint16(exif_data, 2, endian) != 42:
        return []
    first_ifd = read_uint32(exif_data, 4, endian)
    tags = parse_ifd(exif_data, first_ifd, endian, set())
    results: list[tuple[str, str]] = []
    for tag, raw_value in tags.items():
        if tag == 0x9286:
            text = decode_user_comment(raw_value)
        elif tag == 0x010E:
            text = decode_ascii(raw_value)
        elif tag in {0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F}:
            text = decode_xp(raw_value)
        else:
            continue
        if text:
            results.append((EXIF_TEXT_TAGS.get(tag, hex(tag)), text))
    return results


def parse_ifd(
    data: bytes, offset: int, endian: str, visited: set[int]
) -> dict[int, bytes]:
    type_sizes = {
        1: 1,
        2: 1,
        3: 2,
        4: 4,
        5: 8,
        7: 1,
    }
    results: dict[int, bytes] = {}
    while 0 <= offset < len(data) and offset not in visited:
        visited.add(offset)
        if offset + 2 > len(data):
            break
        count = read_uint16(data, offset, endian)
        cursor = offset + 2
        for _ in range(count):
            if cursor + 12 > len(data):
                break
            tag = read_uint16(data, cursor, endian)
            value_type = read_uint16(data, cursor + 2, endian)
            components = read_uint32(data, cursor + 4, endian)
            value_offset = cursor + 8
            unit_size = type_sizes.get(value_type)
            if unit_size is None:
                cursor += 12
                continue
            total_size = unit_size * components
            if total_size <= 4:
                raw_area = data[value_offset : value_offset + 4]
            else:
                actual_offset = read_uint32(data, value_offset, endian)
                if actual_offset + total_size > len(data):
                    cursor += 12
                    continue
                raw_area = data[actual_offset : actual_offset + total_size]
            if tag == 0x8769 and components == 1:
                sub_offset = read_uint32(data, value_offset, endian)
                results.update(parse_ifd(data, sub_offset, endian, visited))
            else:
                results[tag] = raw_area[:total_size]
            cursor += 12
        next_ifd_position = offset + 2 + 12 * count
        if next_ifd_position + 4 > len(data):
            break
        next_ifd_offset = read_uint32(data, next_ifd_position, endian)
        if next_ifd_offset == 0:
            break
        offset = next_ifd_offset
    return results


def read_uint16(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack(endian + "H", data[offset : offset + 2])[0]


def read_uint32(data: bytes, offset: int, endian: str) -> int:
    return struct.unpack(endian + "I", data[offset : offset + 4])[0]


def decode_ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore")


def decode_user_comment(raw: bytes) -> str:
    if len(raw) < 8:
        return decode_best(raw)
    prefix = raw[:8]
    content = raw[8:]
    if prefix.startswith(b"ASCII"):
        return content.rstrip(b"\x00").decode("latin-1", errors="ignore")
    if prefix.startswith(b"UNICODE"):
        encoding = "utf-16be" if len(content) >= 2 and content[0] == 0 else "utf-16le"
        return content.decode(encoding, errors="ignore").strip("\x00")
    if prefix.startswith(b"JIS"):
        try:
            return content.rstrip(b"\x00").decode("ms932")
        except Exception:
            return content.rstrip(b"\x00").decode("shift_jis", errors="ignore")
    return decode_best(content)


def decode_xp(raw: bytes) -> str:
    if raw.endswith(b"\x00\x00"):
        raw = raw[:-2]
    return raw.decode("utf-16le", errors="ignore")


def decode_best(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16le", "utf-16be", "ms932", "shift_jis", "latin-1"):
        try:
            text = data.decode(encoding)
        except Exception:
            continue
        if text:
            return text
    return data.decode("latin-1", errors="ignore")


def extract_xmp_attributes(text: str) -> dict[str, str]:
    results: dict[str, str] = {}
    if not text:
        return results
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        root = None

    if root is not None:
        for elem in root.iter():
            for attr_name, attr_value in elem.attrib.items():
                local_name = attr_name.split("}")[-1]
                if local_name in {"parameters", "Parameters", "sd-metadata", "sd_metadata"}:
                    results[local_name] = html.unescape(attr_value.strip())
    if not results:
        for match in re.finditer(
            r'([A-Za-z_:.-]*(?:parameters|Parameters|sd-metadata|sd_metadata))="([^"]+)"',
            text,
        ):
            results[match.group(1).split(":")[-1]] = html.unescape(match.group(2))
    return results


def choose_first_text(candidates: list[str]) -> str | None:
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        return candidate
    return None


def normalize_fields(raw: dict[str, str]) -> dict:
    fields: dict[str, object] = {}
    parameters_text = raw.get("parameters")
    if parameters_text:
        fields["parameters_raw"] = parameters_text

    for key in ("prompt", "Prompt", "negative_prompt", "Negative prompt"):
        value = raw.get(key)
        if value:
            normalized_key = key.lower().replace(" ", "_")
            fields[normalized_key] = value

    for key, value in raw.items():
        if key in {"prompt", "Prompt", "negative_prompt", "Negative prompt"}:
            continue
        if key not in fields and key not in {"XMP", "Comment", "UserComment", "XPComment", "ImageDescription"}:
            fields[key] = value
        parsed_json = maybe_parse_json(value)
        if parsed_json is not None:
            fields[f"{key}_json"] = parsed_json

    fields.update(extract_comfy_fields(fields))

    return fields


def maybe_parse_json(value: str):
    text = value.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_comfy_fields(fields: dict) -> dict[str, object]:
    candidates = []

    prompt_json = fields.get("prompt_json")
    if is_comfy_graph(prompt_json):
        candidates.append(prompt_json)

    workflow_json = fields.get("workflow_json")
    workflow_graph = workflow_nodes_to_graph(workflow_json)
    if is_comfy_graph(workflow_graph):
        candidates.append(workflow_graph)

    for value in fields.values():
        if not isinstance(value, dict):
            continue
        nested_prompt = value.get("prompt")
        if is_comfy_graph(nested_prompt):
            candidates.append(nested_prompt)
        nested_workflow = workflow_nodes_to_graph(value.get("workflow"))
        if is_comfy_graph(nested_workflow):
            candidates.append(nested_workflow)

    for graph in candidates:
        extracted = extract_from_comfy_graph(graph)
        if extracted:
            return extracted
    return {}


def is_comfy_graph(value) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    return any(
        isinstance(node, dict) and isinstance(node.get("class_type"), str)
        for node in value.values()
    )


def workflow_nodes_to_graph(value):
    if not isinstance(value, dict):
        return None
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        return None
    graph = {}
    for node in nodes:
        if isinstance(node, dict) and node.get("id") is not None:
            graph[str(node["id"])] = node
    return graph or None


def extract_from_comfy_graph(graph: dict) -> dict[str, object]:
    sampler_node = None
    for node in graph.values():
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type.startswith("KSampler"):
            sampler_node = node
            break
    if sampler_node is None:
        return {}

    extracted: dict[str, object] = {"generator": "ComfyUI"}
    inputs = sampler_node.get("inputs", {})
    if inputs.get("seed") is not None:
        extracted["Seed"] = inputs["seed"]
    if inputs.get("steps") is not None:
        extracted["Steps"] = inputs["steps"]
    if inputs.get("cfg") is not None:
        extracted["CFG scale"] = inputs["cfg"]
    if inputs.get("sampler_name") is not None:
        extracted["Sampler"] = inputs["sampler_name"]
    if inputs.get("scheduler") is not None:
        extracted["scheduler"] = inputs["scheduler"]
    if inputs.get("denoise") is not None:
        extracted["denoise"] = inputs["denoise"]

    positive = resolve_comfy_text(graph, inputs.get("positive"))
    negative = resolve_comfy_text(graph, inputs.get("negative"))
    if positive:
        extracted["prompt"] = positive
    if negative:
        extracted["negative_prompt"] = negative
    return extracted


def resolve_comfy_text(graph: dict, connection):
    if connection is None:
        return None
    source_id = connection[0] if isinstance(connection, list) and connection else connection
    node = graph.get(str(source_id))
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs", {})
    text = inputs.get("text")
    if isinstance(text, str):
        return text
    parts = []
    if isinstance(inputs.get("text_g"), str):
        parts.append(inputs["text_g"])
    if isinstance(inputs.get("text_l"), str):
        parts.append(inputs["text_l"])
    if parts:
        return " ".join(parts)
    return None
