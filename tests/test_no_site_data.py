"""Static guarantee that no site data reaches this public repository.

An audio matrix stores personal information and hands it back on every poll: the output names
are room names, the input names are source names, and the system queries return the LAN address
and MAC. Any of it committed here is published, permanently, to a public repo that auto-pushes.

Three layers:

* **Addresses.** Only documentation and loopback ranges may appear in a committed file. A real
  RFC1918 address that is not one of the fixtures' invented ones fails.
* **MAC addresses.** The matrices answer ``Get MAC Add`` with a real one. Only the simulator's
  invented MAC is allowed.
* **Named terms.** If ``local/site-terms.txt`` exists it is read as a newline-separated denylist
  and every term is searched for, case-insensitively. That file is gitignored, so the denylist
  itself never enters the repo -- which is the whole point, since the terms are the secret.

The method matters as much as the check. Auditing by grepping for values already known misses
whatever was not thought of; this enumerates every committed file instead.

Adapted from the equivalent guard in the ha-avpro-edge repository, where it was written after an
audit that grepping had already passed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Addresses that may appear. 192.0.2.x is TEST-NET-1, the documentation range; the rest are
#: loopback and unspecified.
ALLOWED_ADDRESSES = {"127.0.0.1", "0.0.0.0", "255.255.255.0"}
ALLOWED_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")

_RFC1918 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")

#: The simulator's invented MAC. Anything else that looks like one is suspect -- including the
#: real OUI prefix these matrices ship with, which is why that prefix belongs in the gitignored
#: denylist and not in a comment here. This guard caught exactly that leak on its first run.
ALLOWED_MACS = {"AA:BB:CC:DD:EE:FF"}

TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini", ""}


def _git(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def _tracked_files() -> list[Path]:
    """Everything that is committed *or about to be*.

    Untracked-but-not-ignored files are included deliberately. A guard that only looked at what
    is already committed would first complain one commit after the leak, which is exactly too
    late.
    """
    paths = [*_git("ls-files"), *_git("ls-files", "--others", "--exclude-standard")]
    return [ROOT / line for line in dict.fromkeys(paths)]


def _contents() -> list[tuple[Path, str]]:
    out = []
    for path in _tracked_files():
        if not (path.is_file() and path.suffix.lower() in TEXT_SUFFIXES):
            continue
        try:
            out.append((path, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return out


def test_the_audit_enumerates_a_real_set_of_files() -> None:
    """A guard that scans nothing passes forever."""
    scanned = _contents()
    assert len(scanned) > 5
    assert any(path.name == "protocol.py" for path, _ in scanned)


def test_no_private_network_address_is_committed() -> None:
    offenders = []
    for path, text in _contents():
        for number, line in enumerate(text.splitlines(), start=1):
            for found in _RFC1918.findall(line):
                if found in ALLOWED_ADDRESSES or found.startswith(ALLOWED_PREFIXES):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {found}")
    assert not offenders, "private address in a public repo:\n" + "\n".join(offenders)


def test_no_unexpected_mac_address_is_committed() -> None:
    offenders = []
    for path, text in _contents():
        for number, line in enumerate(text.splitlines(), start=1):
            for found in _MAC.findall(line):
                if found.upper() in ALLOWED_MACS:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {found}")
    assert not offenders, "MAC address in a public repo:\n" + "\n".join(offenders)


def test_no_denylisted_site_term_is_committed() -> None:
    """Reads the gitignored ``local/site-terms.txt``, so the denylist never enters the repo.

    Skips silently when the file is absent: a fresh clone has no local/ directory, and this must
    not turn into a failure that trains people to ignore it.
    """
    denylist_path = ROOT / "local" / "site-terms.txt"
    if not denylist_path.exists():
        return

    terms = [
        line.strip().lower()
        for line in denylist_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not terms:
        return

    offenders = []
    for path, text in _contents():
        lowered = text.lower()
        # Report the file and that a term matched, never the surrounding line -- a failure
        # message is itself output, and this one would quote the thing being protected.
        offenders.extend(
            f"{path.relative_to(ROOT)}: contains a denylisted term"
            for term in terms
            if term in lowered
        )
    assert not offenders, "site data in a public repo:\n" + "\n".join(sorted(set(offenders)))


def test_the_gitignore_keeps_local_and_probe_output_out() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "local/" in ignored
    assert "scripts/output/" in ignored


def test_no_tracked_file_lives_under_local() -> None:
    assert not [p for p in _tracked_files() if "local" in p.relative_to(ROOT).parts[:1]]
