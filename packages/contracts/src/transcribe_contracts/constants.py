"""Constants of the R2 data contract."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# Version of the data contract written into every JSON object; readers refuse another major.
SCHEMA_VERSION: Final = "1.0"
# Major part of SCHEMA_VERSION; a new major means an incompatible change.
SCHEMA_MAJOR: Final = 1
# Shape of a schema version string ("<major>.<minor>").
SCHEMA_VERSION_PATTERN: Final = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

# Key prefix of the manifests; a manifest there means "batch ready for the worker".
BATCH_METADATA_PREFIX: Final = "batch_metadata/"
# Suffix of manifest and result objects.
JSON_SUFFIX: Final = ".json"
# Suffix of subtitle objects.
SRT_SUFFIX: Final = ".srt"
# Sub-folder of a batch holding the uploaded audio.
AUDIO_FOLDER: Final = "audio"
# Sub-folder of a batch holding the subtitles and job results.
RESULTS_FOLDER: Final = "results"
# Object name of the batch summary written by the worker after the last job.
DONE_OBJECT_NAME: Final = "_done.json"
# Key prefix of per-run objects written by the worker.
RUNS_PREFIX: Final = "runs/"
# Object name of a run's heartbeat and progress.
RUN_STATUS_OBJECT_NAME: Final = "status.json"
# Object name of a run's log.
RUN_LOG_OBJECT_NAME: Final = "worker.log"
# Characters that would split a key segment into folders (S3 uses "/"; "\\" is refused too).
KEY_SEPARATORS: Final = frozenset("/\\")
# Prefix of the audio track marker in an audio object name ("<ulid>.a<track>.<ext>").
TRACK_MARKER: Final = "a"

# Batch id: "batch_YYYYMMDD_HHmmss", with "_<n>" appended on a same-second collision.
BATCH_ID_PATTERN: Final = r"^batch_[0-9]{8}_[0-9]{6}(?:_[2-9]|_[1-9][0-9]+)?$"
# ULID in canonical Crockford base32 upper case (item ids and run ids).
ULID_PATTERN: Final = r"^[0-9A-HJKMNP-TV-Z]{26}$"
# Audio file extension without the dot; lower case so keys are canonical.
AUDIO_EXTENSION_PATTERN: Final = r"^[a-z0-9]{1,10}$"

# Shape of a language code in stored objects (ISO 639-1, or 639-3 such as "haw", "yue").
LANGUAGE_CODE_PATTERN: Final = r"^[a-z]{2,3}$"

# Languages Whisper large-v3 can transcribe, code -> English name (from openai/whisper tokenizer).
WHISPER_LANGUAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "en": "English",
        "zh": "Chinese",
        "de": "German",
        "es": "Spanish",
        "ru": "Russian",
        "ko": "Korean",
        "fr": "French",
        "ja": "Japanese",
        "pt": "Portuguese",
        "tr": "Turkish",
        "pl": "Polish",
        "ca": "Catalan",
        "nl": "Dutch",
        "ar": "Arabic",
        "sv": "Swedish",
        "it": "Italian",
        "id": "Indonesian",
        "hi": "Hindi",
        "fi": "Finnish",
        "vi": "Vietnamese",
        "he": "Hebrew",
        "uk": "Ukrainian",
        "el": "Greek",
        "ms": "Malay",
        "cs": "Czech",
        "ro": "Romanian",
        "da": "Danish",
        "hu": "Hungarian",
        "ta": "Tamil",
        "no": "Norwegian",
        "th": "Thai",
        "ur": "Urdu",
        "hr": "Croatian",
        "bg": "Bulgarian",
        "lt": "Lithuanian",
        "la": "Latin",
        "mi": "Maori",
        "ml": "Malayalam",
        "cy": "Welsh",
        "sk": "Slovak",
        "te": "Telugu",
        "fa": "Persian",
        "lv": "Latvian",
        "bn": "Bengali",
        "sr": "Serbian",
        "az": "Azerbaijani",
        "sl": "Slovenian",
        "kn": "Kannada",
        "et": "Estonian",
        "mk": "Macedonian",
        "br": "Breton",
        "eu": "Basque",
        "is": "Icelandic",
        "hy": "Armenian",
        "ne": "Nepali",
        "mn": "Mongolian",
        "bs": "Bosnian",
        "kk": "Kazakh",
        "sq": "Albanian",
        "sw": "Swahili",
        "gl": "Galician",
        "mr": "Marathi",
        "pa": "Punjabi",
        "si": "Sinhala",
        "km": "Khmer",
        "sn": "Shona",
        "yo": "Yoruba",
        "so": "Somali",
        "af": "Afrikaans",
        "oc": "Occitan",
        "ka": "Georgian",
        "be": "Belarusian",
        "tg": "Tajik",
        "sd": "Sindhi",
        "gu": "Gujarati",
        "am": "Amharic",
        "yi": "Yiddish",
        "lo": "Lao",
        "uz": "Uzbek",
        "fo": "Faroese",
        "ht": "Haitian Creole",
        "ps": "Pashto",
        "tk": "Turkmen",
        "nn": "Nynorsk",
        "mt": "Maltese",
        "sa": "Sanskrit",
        "lb": "Luxembourgish",
        "my": "Myanmar",
        "bo": "Tibetan",
        "tl": "Tagalog",
        "mg": "Malagasy",
        "as": "Assamese",
        "tt": "Tatar",
        "haw": "Hawaiian",
        "ln": "Lingala",
        "ha": "Hausa",
        "ba": "Bashkir",
        "jw": "Javanese",
        "su": "Sundanese",
        "yue": "Cantonese",
    },
)
# Languages with a default WhisperX alignment model (whisperx 3.8.6 alignment.py).
ALIGNMENT_LANGUAGES: Final = frozenset(
    (
        "ar",
        "ca",
        "cs",
        "da",
        "de",
        "el",
        "en",
        "es",
        "eu",
        "fa",
        "fi",
        "fr",
        "gl",
        "he",
        "hi",
        "hr",
        "hu",
        "id",
        "it",
        "ja",
        "ka",
        "ko",
        "lv",
        "ml",
        "nl",
        "nn",
        "no",
        "pl",
        "pt",
        "ro",
        "ru",
        "sk",
        "sl",
        "sv",
        "te",
        "tl",
        "tr",
        "uk",
        "ur",
        "vi",
        "zh",
    ),
)
# Languages enabled in a fresh installation.
DEFAULT_LANGUAGES: Final = ("hu", "en")
