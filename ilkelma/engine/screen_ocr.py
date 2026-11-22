"""Read visible text with Windows OCR. All coordinates are physical pixels."""
import asyncio
from collections import Counter
from dataclasses import dataclass
from hashlib import blake2b
from math import ceil, floor
from secrets import token_bytes
from statistics import median
import re
import unicodedata

from PIL import Image, ImageGrab
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

from ilkelma.engine.language import detect
from ilkelma.engine.learned_words import WordStore, tokens
from maltese_fix import ARTICLES, match_case
from ilkelma.engine.maltese_fix import fix
from ilkelma.languages import packs


@dataclass(frozen=True)
class Line:
    text: str
    box: tuple[float, float, float, float]
    brightness: float = 0

    @property
    def height(self):
        return self.box[3] - self.box[1]


def point_box(point, bounds):
    x, y = point
    left, top, right, bottom = bounds
    return max(left, x - 380), max(top, y - 110), min(right, x + 380), min(bottom, y + 110)


def ink_brightness(image, box):
    left, top, right, bottom = box
    crop = image.crop((max(0, floor(left)), max(0, floor(top)),
                       min(image.width, ceil(right)), min(image.height, ceil(bottom))))
    # The median of the brightest fifth avoids dark background and edge pixels.
    remaining = max(1, crop.width * crop.height * .1)
    for level, count in reversed(list(enumerate(crop.histogram()))):
        remaining -= count
        if remaining <= 0:
            return level
    return 0


async def recognize(image, language="mt", *, sample_ink=False):
    preferred = packs().get(language, {}).get("ocr", "en-GB")
    engine = (OcrEngine.try_create_from_language(Language(preferred))
              or OcrEngine.try_create_from_language(Language("en-US"))
              or OcrEngine.try_create_from_user_profile_languages())
    if engine is None:
        raise RuntimeError("Windows OCR needs an installed recognition language.")
    scale = min(2, OcrEngine.max_image_dimension / max(image.size))
    width, height = (max(1, int(size * scale)) for size in image.size)
    pixels = image.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(pixels.tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height,
        BitmapAlphaMode.PREMULTIPLIED)
    try:
        result = await engine.recognize_async(bitmap)
        grayscale = image.convert("L") if sample_ink else None
        lines = []
        for line in result.lines:
            boxes = [word.bounding_rect for word in line.words]
            if boxes:
                left = min(box.x for box in boxes)
                top = min(box.y for box in boxes)
                right = max(box.x + box.width for box in boxes)
                bottom = max(box.y + box.height for box in boxes)
                brightness = 0
                if grayscale is not None:
                    words = list(line.words)
                    while len(words) > 1 and BADGE.fullmatch(words[0].text):
                        words.pop(0)
                    brightness = median(ink_brightness(
                        grayscale, (box.x / scale, box.y / scale,
                                    (box.x + box.width) / scale, (box.y + box.height) / scale))
                        for box in (word.bounding_rect for word in words))
                lines.append(Line(line.text, (left / scale, top / scale, right / scale, bottom / scale),
                                  brightness))
        return sorted(lines, key=lambda line: (line.box[1], line.box[0]))
    finally:
        bitmap.close()
        writer.close()


def capture(box, language, excluded=(), *, sample_ink=False):
    image = ImageGrab.grab(bbox=box, all_screens=True)
    left, top = box[:2]
    return [line for line in asyncio.run(recognize(image, language, sample_ink=sample_ink))
            if not any(line.box[0] + left < x2 and line.box[2] + left > x1
                       and line.box[1] + top < y2 and line.box[3] + top > y1
                       for x1, y1, x2, y2 in excluded)]


def pointed_text(lines, point):
    x, y = point

    def distance(line):
        left, top, right, bottom = line.box
        return max(left - x, x - right, 0) + 2 * max(top - y, y - bottom, 0)

    if not lines:
        return ""
    closest = min(lines, key=distance)
    if distance(closest) > 45:
        return ""
    left, _, right, _ = closest.box
    column = [line for line in lines if line.box[0] < right + 12 and line.box[2] > left - 12]
    index = start = end = column.index(closest)

    def connected(upper, lower):
        gap = lower.box[1] - upper.box[3]
        return (0 <= gap <= max(5, min(upper.height, lower.height) * .95)
                and abs(upper.box[0] - lower.box[0]) <= max(12, closest.height * 1.4))

    while start > max(0, index - 4) and connected(column[start - 1], column[start]):
        start -= 1
    while end + 1 < min(len(column), index + 5) and connected(column[end], column[end + 1]):
        end += 1
    return " ".join(line.text for line in column[start:end + 1])


SYSTEM = re.compile(
    r"^(?:welcome to (?:the )?[\w .]+? live\b|(?:.+? )?(?:joined(?: the live)?|liked the live)"
    r"[.! ]*$|(?:.+? )?sent (?:a |\d+ )?(?:gift|likes?)\b)", re.IGNORECASE)


def looks_like_name(text):
    return bool(text and len(text) <= 40 and len(text.split()) <= 4 and text[-1] not in "!?.,:;")


def comments(lines, locations=None):
    """Pair a short name with the line below it and keep tightly wrapped text together."""
    pairs = []
    name = None
    previous = None
    for index, line in enumerate(lines):
        value = line.text.strip()
        if not value or SYSTEM.search(value):
            name = previous = None
            continue
        short_name = looks_like_name(value)
        gap = line.box[1] - previous.box[3] if previous else float("inf")
        following = lines[index + 1] if index + 1 < len(lines) else None
        # A crop can begin halfway through a comment. Prefer the next label
        # when its text is much closer than the line above it.
        starts_pair = (short_name and previous and following
                       and line.height <= previous.height * 1.15 and gap >= 10
                       and not SYSTEM.search(following.text.strip())
                       and 0 <= following.box[1] - line.box[3] < gap * .65)
        if name and not starts_pair and gap <= max(18, previous.height * 1.8):
            pairs.append((name, value))
            if locations is not None:
                locations.append((previous, [line]))
            name = None
        elif (previous and pairs and not starts_pair and gap <= max(4, line.height * .55)
              and abs(line.box[0] - previous.box[0]) <= 12
              and (not short_name or line.height >= previous.height * .9)):
            author, message = pairs[-1]
            pairs[-1] = author, f"{message} {value}"
            if locations is not None:
                locations[-1][1].append(line)
        else:
            name = value if short_name else None
        previous = line
    return pairs


def pointed_comment(lines, point, *, in_chat=False):
    locations = []
    pairs = comments(lines, locations)
    x, y = point
    for pair, (label, message_lines) in zip(pairs, locations):
        first = message_lines[0]
        # Outside a selected chat region, require a distinct smaller label.
        if not in_chat and (label.height > first.height * .8
                            or abs(label.box[0] - first.box[0]) > 12):
            continue
        if any(left <= x <= right and top <= y <= bottom
               for left, top, right, bottom in (line.box for line in message_lines)):
            return pair
    return None


_SESSION_KEY = token_bytes(32)

BADGE = re.compile(r"(?:\(all\)|cqsd|cos\)|a\d+|\d+)", re.IGNORECASE)
JOINED = re.compile(r"joined(?: the live)?[.! ]*", re.IGNORECASE)
LIVE_NOTICE = re.compile(r"\bis\s+live\b", re.IGNORECASE)


def clean_text(text):
    letters = sum(char.isalpha() for char in text)
    return letters >= 2 and letters * 2 >= sum(not char.isspace() for char in text)


def clean_comment(text):
    text = unicodedata.normalize("NFC", text).strip()
    return re.sub(r"\b0[kK]\b", lambda match: "OK" if match[0] == "0K" else "ok", text)


def ocr_key(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"\w+", "".join(
        char for char in text if not unicodedata.combining(char))))


def below(upper, lower):
    # OCR sometimes skips the badge and starts the label at the indented name.
    return (0 <= lower.box[1] - upper.box[3] <= max(8, lower.height)
            and -14 <= upper.box[0] - lower.box[0] <= 32)


def read_chat(lines, bubbles=()):
    """Find bright messages and use adjacent, dim labels to identify authors."""
    bubble_keys = {ocr_key(text) for text in bubbles} - {""}
    rows = [line for line in lines if line.box[0] >= 50]
    found = []
    label = None
    for index, line in enumerate(rows):
        value = line.text.strip()
        following = rows[index + 1] if index + 1 < len(rows) else None
        joined_fragment = (following and JOINED.fullmatch(following.text.strip())
                           and len(value) <= 24
                           and abs(line.box[0] - following.box[0]) <= 20
                           and following.box[1] - line.box[3] <= max(8, line.height))
        key = ocr_key(value)
        if (joined_fragment or SYSTEM.search(value) or LIVE_NOTICE.search(value)
                or key == "stop" or any(f" {bubble} " in f" {key} " for bubble in bubble_keys)):
            label = None
            continue
        smaller_and_dimmer = (following and following.brightness >= 200
                              and line.brightness <= following.brightness - 20
                              and line.height <= following.height * .85
                              and below(line, following))
        if line.brightness < 200 or smaller_and_dimmer:
            words = value.split()
            while words and BADGE.fullmatch(words[0]):
                words.pop(0)
            name = " ".join(words)
            label = (name, line) if clean_text(name) and looks_like_name(name) else None
            continue
        name = ""
        if label:
            candidate, upper = label
            if below(upper, line) and upper.height <= line.height * 1.15:
                name = candidate
        label = None
        if clean_text(clean_comment(value)):
            found.append(((name, value), line.box[3]))
    return found


def comment_key(pair):
    normalized = ["".join(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))
                  for text in pair]
    return blake2b("\0".join(normalized).encode("utf-8"), key=_SESSION_KEY, digest_size=16).digest()


class NewComments:
    """Speak unseen messages below an upward-moving anchor."""
    def __init__(self, height):
        self.seen = set()
        self.previous = {}
        self.bottom = height * 2 / 3

    def update(self, comments):
        rows = [(blake2b(ocr_key(pair[1]).encode("utf-8"), key=_SESSION_KEY, digest_size=16).digest(),
                 pair, y) for pair, y in comments]
        counts = Counter(key for key, _, _ in rows)
        positions = {key: y for key, _, y in rows if counts[key] == 1}
        shared = [(key, y) for key, y in positions.items() if key in self.previous]
        # The chat either scrolls up (full chat) or grows downwards (chat not full yet).
        # In both cases a new message appears below the lowest message we saw before.
        # Messages that move down (user scrolled back up) are old and are not read.
        steady = shared and all(y - self.previous[key] < 3 for key, y in shared)
        anchor = max((y for _, y in shared), default=0)
        fresh = []
        for key, pair, y in rows:
            if steady and y > anchor and key not in self.seen:
                fresh.append(pair)
            self.seen.add(key)
        self.previous = positions
        return fresh


def apply_patterns(text, patterns):
    parts = []
    end = 0
    words = list(tokens(text))
    index = 0
    while index < len(words):
        raw, word, _, start, stop = words[index]
        change = patterns.get(word)
        index += 1
        if word in ARTICLES and index < len(words):
            next_raw, next_word, _, next_start, next_stop = words[index]
            joined = patterns.get(word + next_word)
            if (next_raw.islower() and text[stop:next_start].isspace()
                    and joined == word + "-" + next_word):
                change, stop = joined, next_stop
                index += 1
        if change and (raw.islower() or raw.istitle()):
            parts.extend((text[end:start], match_case(raw, change)))
            end = stop
    return "".join(parts) + text[end:]


def prepare(text, fallback, corrections):
    language = detect(text, default=fallback or "mt")
    if language == "mt":
        text = prepare_maltese(text, corrections)
    return text, language


def prepare_maltese(text, corrections):
    text = unicodedata.normalize("NFC", text)
    text = apply_patterns(text, WordStore().spelling())
    return corrections(fix(text, lang="mt"), "mt")
