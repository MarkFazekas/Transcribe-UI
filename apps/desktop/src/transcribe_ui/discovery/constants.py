"""Constants of the scan and the selection (architecture §3.2)."""

import stat
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# Video file extensions classified as video by default (lower case, no dot).
DEFAULT_VIDEO_EXTENSIONS: Final = (
    "3gp",
    "avi",
    "flv",
    "m2ts",
    "m4v",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "ogv",
    "ts",
    "vob",
    "webm",
    "wmv",
)
# Audio file extensions classified as audio by default (lower case, no dot).
DEFAULT_AUDIO_EXTENSIONS: Final = (
    "aac",
    "ac3",
    "aif",
    "aiff",
    "amr",
    "ape",
    "dts",
    "eac3",
    "flac",
    "m4a",
    "m4b",
    "mka",
    "mp2",
    "mp3",
    "oga",
    "ogg",
    "opus",
    "wav",
    "wma",
    "wv",
)
# Extensions of sidecar subtitle files (lower case, no dot); not configurable.
SIDECAR_EXTENSIONS: Final = ("ass", "srt", "ssa", "sub", "vtt")
# Shape of a configurable media extension: lower case letters and digits, no dot.
EXTENSION_PATTERN: Final = r"^[a-z0-9]{1,16}$"

# Sidecar name token marking a forced subtitle (`<stem>.<lang>.forced.srt`).
FORCED_MARKER: Final = "forced"
# Separators between a language and its region or script in a sidecar token (`pt-BR`, `en_US`).
LANGUAGE_REGION_SEPARATORS: Final = ("-", "_")

# ISO 639-2 (bibliographic and terminological) and 639-3 codes -> Whisper language code.
ISO_THREE_LETTER_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "afr": "af",
        "alb": "sq",
        "amh": "am",
        "ara": "ar",
        "arm": "hy",
        "asm": "as",
        "aze": "az",
        "bak": "ba",
        "baq": "eu",
        "bel": "be",
        "ben": "bn",
        "bod": "bo",
        "bos": "bs",
        "bre": "br",
        "bul": "bg",
        "bur": "my",
        "cat": "ca",
        "ces": "cs",
        "chi": "zh",
        "cmn": "zh",
        "cym": "cy",
        "cze": "cs",
        "dan": "da",
        "deu": "de",
        "dut": "nl",
        "ell": "el",
        "eng": "en",
        "est": "et",
        "eus": "eu",
        "fao": "fo",
        "fas": "fa",
        "fil": "tl",
        "fin": "fi",
        "fra": "fr",
        "fre": "fr",
        "geo": "ka",
        "ger": "de",
        "glg": "gl",
        "gre": "el",
        "guj": "gu",
        "hat": "ht",
        "hau": "ha",
        "haw": "haw",
        "heb": "he",
        "hin": "hi",
        "hrv": "hr",
        "hun": "hu",
        "hye": "hy",
        "ice": "is",
        "ind": "id",
        "isl": "is",
        "ita": "it",
        "jav": "jw",
        "jpn": "ja",
        "kan": "kn",
        "kat": "ka",
        "kaz": "kk",
        "khm": "km",
        "kor": "ko",
        "lao": "lo",
        "lat": "la",
        "lav": "lv",
        "lin": "ln",
        "lit": "lt",
        "ltz": "lb",
        "mac": "mk",
        "mal": "ml",
        "mao": "mi",
        "mar": "mr",
        "may": "ms",
        "mkd": "mk",
        "mlg": "mg",
        "mlt": "mt",
        "mon": "mn",
        "mri": "mi",
        "msa": "ms",
        "mya": "my",
        "nep": "ne",
        "nld": "nl",
        "nno": "nn",
        "nob": "no",
        "nor": "no",
        "oci": "oc",
        "pan": "pa",
        "per": "fa",
        "pol": "pl",
        "por": "pt",
        "pus": "ps",
        "ron": "ro",
        "rum": "ro",
        "rus": "ru",
        "san": "sa",
        "sin": "si",
        "slk": "sk",
        "slo": "sk",
        "slv": "sl",
        "sna": "sn",
        "snd": "sd",
        "som": "so",
        "spa": "es",
        "sqi": "sq",
        "srp": "sr",
        "sun": "su",
        "swa": "sw",
        "swe": "sv",
        "tam": "ta",
        "tat": "tt",
        "tel": "te",
        "tgk": "tg",
        "tgl": "tl",
        "tha": "th",
        "tib": "bo",
        "tuk": "tk",
        "tur": "tr",
        "ukr": "uk",
        "urd": "ur",
        "uzb": "uz",
        "vie": "vi",
        "wel": "cy",
        "yid": "yi",
        "yor": "yo",
        "yue": "yue",
        "zho": "zh",
    },
)
# Other spellings of languages in sidecar names (old ISO 639-1 codes, common English names that
# differ from Whisper's) -> Whisper language code. Whisper's own English names are known anyway.
LANGUAGE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "bokmal": "no",
        "brazilian": "pt",
        "burmese": "my",
        "castilian": "es",
        "farsi": "fa",
        "filipino": "tl",
        "flemish": "nl",
        "iw": "he",
        "jv": "jw",
        "mandarin": "zh",
        "moldovan": "ro",
        "nb": "no",
        "panjabi": "pa",
        "pushto": "ps",
        "sinhalese": "si",
        "valencian": "ca",
    },
)

# Deepest folder level below the scan root that is scanned; deeper folders give a warning.
MAX_SCAN_DEPTH: Final = 256
# Media files found between two progress events of a scan.
SCAN_PROGRESS_INTERVAL: Final = 1000
# Progress stage name of the folder walk.
SCAN_STAGE: Final = "scan"
# Longest text of a name filter (characters).
NAME_FILTER_MAX_LENGTH: Final = 1000

# Error code: the scan root is not an absolute path.
ERROR_ROOT_NOT_ABSOLUTE: Final = "root_not_absolute"
# Error code: the scan root does not exist.
ERROR_ROOT_NOT_FOUND: Final = "root_not_found"
# Error code: the scan root exists but is not a folder.
ERROR_ROOT_NOT_A_FOLDER: Final = "root_not_a_folder"
# Error code: a selection path is not below the scan root (or contains `..`).
ERROR_PATH_OUTSIDE_ROOT: Final = "path_outside_root"

# Windows file attributes that make an entry hidden from the scan (unless hidden ones are asked).
HIDDEN_FILE_ATTRIBUTES: Final = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
# Prefix of hidden names on every OS.
HIDDEN_NAME_PREFIX: Final = "."
