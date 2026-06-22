"""Shared configuration: required requirement fields, option sets, and synthesis limits."""

# The four things we need before we can design a dataset.
REQUIRED_FIELDS = ("topic", "complexity", "format", "rows")

# Option cards offered for the fields the user can pick from.
COMPLEXITY_OPTIONS = ("Simple", "Medium", "Hard")
FORMAT_OPTIONS = ("CSV", "JSON", "Markdown")
ROW_OPTIONS = ("10", "50", "100", "500")

# Cap per-table synthesis so a runaway spec can't exhaust memory.
MAX_ROWS_PER_TABLE = 100_000

# Locales each row may draw from so name/address/phone/country stay mutually consistent.
DEFAULT_LOCALES = (
    "en_US",
    "en_GB",
    "en_CA",
    "en_AU",
    "de_DE",
    "fr_FR",
    "es_ES",
    "it_IT",
)
