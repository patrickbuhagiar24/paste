"""The project moves forward: no backward-compatibility layers, versioned state.

DEVELOPMENT.md states the policy. This file enforces the two rules that a
machine can check, so a compatibility branch cannot slip in unnoticed:

1. no backward compatibility in shipped code;
2. every persisted shape carries an explicit version, and an unknown version is
   discarded rather than read field by field.
"""

import re

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
SCRIPTS = ROOT / "scripts"

# Words that mean "we kept the old way around". Matched case-insensitively in
# shipped JavaScript. Adding an entry here is a decision, not a workaround.
LEGACY_MARKERS = (
    r"\blegacy\b",
    r"\bbackward compat",
    r"\bbackwards compat",
    r"\bcompatibility with\b",
    r"\bolder librar",
    r"\bold format\b",
    r"\bstill read\b",
    r"\bstill accepted\b",
    r"\bfor older (?:librar|version|snapshot|caller)",
)


def _shipped_sources():
    """Every JavaScript file the extension actually loads."""
    return sorted(EXT.glob("*.js"))


def test_no_compatibility_layer_in_shipped_code():
    pattern = re.compile("|".join(LEGACY_MARKERS), re.IGNORECASE)
    for path in _shipped_sources():
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            snippet = text.splitlines()[line - 1].strip()
            raise AssertionError(
                f"{path.name}:{line} keeps a compatibility path: {snippet!r}\n"
                "DEVELOPMENT.md: replace the old mechanism, do not support both."
            )


def test_no_feature_detection_for_old_browser_versions():
    """A minimum version is a floor, not a suggestion: no capability branches."""
    for path in _shipped_sources():
        text = path.read_text(encoding="utf-8")
        for banned in ("typeof chrome.sidePanel.close", "panelToggleSupported",
                       "openPanelOnActionClick: true, // fallback"):
            assert banned not in text, f"{path.name} still branches on {banned!r}"


def test_persisted_shapes_are_versioned():
    """Each stored shape names its own version, and rejects an unknown one."""
    library = (EXT / "library.js").read_text(encoding="utf-8")
    assert "INDEX_VERSION" in library, "pastex_index has no version constant"
    assert re.search(r'data\.v\s*!==?\s*INDEX_VERSION', library), (
        "parseIndex must compare the stored version with INDEX_VERSION"
    )

    config = (EXT / "config-source.js").read_text(encoding="utf-8")
    assert "CACHE_VERSION" in config, "the configuration cache has no version"
    assert re.search(r"record\.version\s*!==?\s*CACHE_VERSION", config), (
        "the configuration cache reader must reject another version"
    )

    # The queue is versioned by its storage key: a new shape gets a new key, and
    # the old key is simply never read. That is the same rule, one level up.
    queue = (EXT / "queue.js").read_text(encoding="utf-8")
    key = re.search(r'var KEY = "([^"]+)"', queue)
    assert key, "the queue has no storage key"
    assert re.search(r"\.v\d+$", key.group(1)), (
        f"the queue key {key.group(1)!r} does not carry a version"
    )
    assert queue.count("var KEY =") == 1, "the queue must read exactly one key"


def test_unknown_state_is_rebuilt_not_guessed():
    """A version mismatch discards the state; it never fills in fields."""
    library = (EXT / "library.js").read_text(encoding="utf-8")
    parse = library.split("function parseIndex", 1)[1].split("\n  }", 1)[0]
    assert "emptyIndex()" in parse, "parseIndex must fall back to an empty index"
    assert "pastex_order" not in parse, "parseIndex must not read per-item labels"

    config = (EXT / "config-source.js").read_text(encoding="utf-8")
    assert "return null" in config, (
        "an unknown cache version must be treated as an absent cache"
    )


# Words that mean a release takes something away from a user who already has
# Pastex installed. Any of them in the newest changelog entry requires a
# BREAKING: line next to it.
REMOVAL_WORDS = (
    "deleted", "removed", "dropped", "ignored", "renamed", "replaced",
    "no longer", "reset", "discarded", "migrated",
)


def test_breaking_changes_are_announced_in_the_changelog():
    """Anything that takes something away is a BREAKING entry, not a footnote."""
    assert "BREAKING" in (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")

    changelog = (ROOT / "debian" / "changelog").read_text(encoding="utf-8")
    latest = changelog.split("\n -- ", 1)[0]
    lower = latest.lower()
    removals = [word for word in REMOVAL_WORDS if word in lower]
    assert not removals or "BREAKING:" in latest, (
        "the newest changelog entry removes something "
        f"({', '.join(removals)}) without a BREAKING: line explaining what a "
        "user may notice. See DEVELOPMENT.md rule 5."
    )


def test_every_breaking_line_names_what_the_user_loses():
    """"BREAKING:" alone is not an entry: it must say what happens."""
    changelog = (ROOT / "debian" / "changelog").read_text(encoding="utf-8")
    latest = changelog.split("\n -- ", 1)[0]
    paragraphs = re.split(r"\n  \* ", latest)
    for paragraph in paragraphs:
        if "BREAKING:" not in paragraph:
            continue
        body = paragraph.split("BREAKING:", 1)[1].strip()
        assert len(body.split()) >= 8, (
            "a BREAKING: line must say what was dropped and what the user sees: "
            f"{body!r}"
        )


def test_policy_document_states_the_three_classes():
    policy = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")
    for phrase in ("Content", "Presentation metadata", "Cache"):
        assert phrase in policy, f"DEVELOPMENT.md does not classify {phrase}"
    assert "out of bounds" in policy, "the policy must say content is untouched"


def test_reset_script_never_touches_drive():
    script = (SCRIPTS / "reset-pastex-state.sh").read_text(encoding="utf-8")
    assert "Drive" in script or "drive" in script
    for forbidden in ("rm -rf ~", "$HOME/Google", "googleapis"):
        assert forbidden not in script, f"the reset script touches {forbidden!r}"
    assert "extension storage" in script.lower() or "chrome.storage" in script
