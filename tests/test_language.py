"""The project speaks one language: English.

This guard covers the documents, the shipped JavaScript, and the shell scripts
a user runs. It is a word list, not a translator: it catches an Indonesian
sentence that slipped back into a file, which is exactly the failure it can
detect reliably. Historical `debian/changelog` entries are exempt, because a
changelog records what happened under the names that existed then.

The word list deliberately holds only words that have no English spelling
collision, so a false positive cannot force a rewrite of correct English.
"""

import re

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Documents a user reads, plus the code and scripts they run.
LIVING_FILES = (
    "README.md",
    "ARCHITECTURE.md",
    "ANALYSIS.md",
    "DEVELOPMENT.md",
    "SECURITY.md",
    "running-on-linux-mint.md",
    "where-order-and-collapse-live.md",
    "apt-repo/README.md",
    "extension/README.md",
)

CODE_GLOBS = ("extension/*.js", "scripts/*.sh", "tests/*.py", "debian/postinst", "debian/postrm")

# Indonesian function words and everyday nouns. None of these are English words.
INDONESIAN = (
    "yang", "untuk", "tidak", "dengan", "adalah", "sudah", "belum", "harus",
    "karena", "atau", "jika", "juga", "hanya", "bisa", "berkas", "tombol",
    "halaman", "pengguna", "tautan", "unduh", "unggah", "perubahan",
    "memakai", "dipakai", "tersebut", "sebagai", "antara", "masih", "selalu",
    "mungkin", "supaya", "sebelum", "sesudah", "bilah", "pohon", "caranya",
)

PATTERN = re.compile(r"\b(?:" + "|".join(INDONESIAN) + r")\b", re.IGNORECASE)


def _living_paths():
    """Files the rule applies to. This guard lists the banned words, so it is
    the one file that naturally contains them."""
    paths = [ROOT / name for name in LIVING_FILES]
    for glob in CODE_GLOBS:
        paths.extend(sorted(ROOT.glob(glob)))
    me = Path(__file__).resolve()
    return [path for path in paths if path.is_file() and path.resolve() != me]


def test_living_files_and_code_are_english():
    findings = []
    for path in _living_paths():
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            line = text[: match.start()].count("\n") + 1
            findings.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)!r}")
    assert not findings, (
        "Indonesian text is back in a living file:\n  "
        + "\n  ".join(findings[:10])
        + "\nSee DEVELOPMENT.md: one language, English."
    )


def test_documents_are_named_in_english():
    """The project's four long-form documents carry English file names."""
    for name in ("ARCHITECTURE.md", "ANALYSIS.md", "DEVELOPMENT.md", "SECURITY.md"):
        assert (ROOT / name).is_file(), f"{name} is missing"
    for old in ("ARSITEKTUR.md", "ANALISIS.md", "cara-jalankan-get-linux-mint.md",
                "lokasi-collapse-urutan.md"):
        assert not (ROOT / old).exists(), f"{old} still exists; the rename is not a copy"


def test_document_links_point_at_names_that_exist():
    """A renamed document must not leave a dead link in the living docs."""
    pattern = re.compile(r"\]\(([^)#\s]+\.md)\)")
    for path in _living_paths():
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            target = match.group(1)
            if target.startswith("http"):
                continue
            resolved = (path.parent / target).resolve()
            assert resolved.is_file(), (
                f"{path.relative_to(ROOT)} links to {target}, which does not exist"
            )
