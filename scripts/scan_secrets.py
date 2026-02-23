#!/usr/bin/env python3
"""
OCI Secrets Scanner
-------------------
Scans staged or all tracked files for secrets before they reach the remote.
Run directly:   python scripts/scan_secrets.py [--all]
Pre-push hook:  installed automatically via .githooks/pre-push

Exit 0 = clean. Exit 1 = secrets found (push blocked).
"""
import re
import sys
import subprocess
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@dataclass
class Rule:
    name: str
    pattern: re.Pattern
    severity: str       # CRITICAL | HIGH | MEDIUM
    description: str
    # lines matching these patterns are safe to ignore (false-positive suppression)
    allowlist: List[re.Pattern] = field(default_factory=list)


RULES: List[Rule] = [
    # --- OCI-specific ---
    Rule(
        name="OCI_REAL_OCID",
        pattern=re.compile(
            r'ocid1\.[a-z][a-z0-9-]{1,32}\.(oc[0-9]+|iad|phx|fra|lhr|nrt|syd|yyz|sgp|bom|icn|mel)\.'
            r'[a-z0-9-]*\.'
            r'[a-z0-9]{40,}',   # real unique segment is always 40+ base58 chars
        ),
        severity="CRITICAL",
        description="Real OCI OCID (40+ char unique segment)",
        allowlist=[
            re.compile(r'xxxx|aaaa|example|placeholder|YOUR_|<|>|\.\.\.',re.I),
            re.compile(r'aaaaaaaxxxxxxxxx'),
        ],
    ),
    Rule(
        name="OCI_API_KEY_FINGERPRINT",
        pattern=re.compile(r'\b([0-9a-f]{2}:){19}[0-9a-f]{2}\b'),
        severity="CRITICAL",
        description="OCI API key fingerprint (20 colon-separated hex pairs)",
    ),
    Rule(
        name="OCI_AUTH_TOKEN",
        pattern=re.compile(r'["\']([A-Za-z0-9+/]{60,}={0,2})["\']'),
        severity="HIGH",
        description="Possible base64 auth token (60+ chars)",
        allowlist=[
            re.compile(r'example|placeholder|YOUR_|test|sample|<|>', re.I),
            # known long base64 strings that are not secrets
            re.compile(r'dGVzdA|aGVsbG8|Zm9v'),
        ],
    ),
    Rule(
        name="PRIVATE_KEY_HEADER",
        pattern=re.compile(r'-----BEGIN (RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----'),
        severity="CRITICAL",
        description="PEM private key block",
    ),
    Rule(
        name="OCI_TENANCY_OCID_IN_CODE",
        pattern=re.compile(
            r'tenancy_ocid\s*[=:]\s*["\']ocid1\.tenancy\.[^"\']{20,}["\']'
        ),
        severity="CRITICAL",
        description="Hardcoded tenancy OCID in code",
        allowlist=[re.compile(r'example|xxxx|var\.|os\.getenv', re.I)],
    ),

    # --- Generic secrets ---
    Rule(
        name="HARDCODED_PASSWORD",
        pattern=re.compile(
            r'(?i)(password|passwd|pwd)\s*[=:]\s*["\'][^"\']{6,}["\']'
        ),
        severity="HIGH",
        description="Hardcoded password in string literal",
        allowlist=[
            re.compile(r'example|placeholder|YOUR_|<password>|os\.getenv|env\[', re.I),
            re.compile(r'OAuth2PasswordBearer|PasswordBearer'),
        ],
    ),
    Rule(
        name="HARDCODED_SECRET",
        pattern=re.compile(
            r'(?i)(secret|api_key|apikey|access_key|auth_token)\s*[=:]\s*["\'][^"\']{8,}["\']'
        ),
        severity="HIGH",
        description="Hardcoded secret/key in string literal",
        allowlist=[
            re.compile(r'example|placeholder|YOUR_|<|>|os\.getenv|env\[|SecretsClient|list_secrets', re.I),
        ],
    ),
    Rule(
        name="AWS_ACCESS_KEY",
        pattern=re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
        severity="CRITICAL",
        description="AWS Access Key ID",
    ),
    Rule(
        name="BEARER_TOKEN",
        pattern=re.compile(r'Bearer\s+[A-Za-z0-9._\-]{40,}'),
        severity="HIGH",
        description="Bearer token value",
        allowlist=[re.compile(r'example|placeholder|<token>', re.I)],
    ),
    Rule(
        name="GITHUB_TOKEN",
        pattern=re.compile(r'ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}'),
        severity="CRITICAL",
        description="GitHub personal access token",
    ),
]

# Files to always skip
SKIP_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
                   '.zip', '.tar', '.gz', '.whl', '.pyc', '.pem'}
SKIP_NAMES = {'.gitignore', 'scan_secrets.py'}

# ---------------------------------------------------------------------------
# Scanning logic
# ---------------------------------------------------------------------------

def _is_allowlisted(line: str, rule: Rule) -> bool:
    return any(al.search(line) for al in rule.allowlist)


def scan_file(path: Path) -> List[dict]:
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return []
    if path.name in SKIP_NAMES:
        return []
    findings = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule in RULES:
            if rule.pattern.search(line) and not _is_allowlisted(line, rule):
                findings.append({
                    "file": str(path),
                    "line": lineno,
                    "rule": rule.name,
                    "severity": rule.severity,
                    "description": rule.description,
                    "snippet": line.strip()[:120],
                })
    return findings


def get_files_to_scan(scan_all: bool) -> List[Path]:
    if scan_all:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True
        )
    else:
        # Only staged files (about to be committed/pushed)
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True
        )
        if not result.stdout.strip():
            # Fall back to HEAD..local changes for pre-push
            result = subprocess.run(
                ["git", "diff", "HEAD", "--name-only", "--diff-filter=ACM"],
                capture_output=True, text=True
            )
    return [Path(f) for f in result.stdout.splitlines() if f.strip()]


def main():
    parser = argparse.ArgumentParser(description="OCI Secrets Scanner")
    parser.add_argument("--all", action="store_true",
                        help="Scan all tracked files (default: staged only)")
    parser.add_argument("--strict", action="store_true",
                        help="Block on HIGH severity too (default: CRITICAL only)")
    args = parser.parse_args()

    files = get_files_to_scan(scan_all=args.all)
    if not files:
        print("scan_secrets: nothing to scan.")
        sys.exit(0)

    all_findings = []
    for f in files:
        if f.exists():
            all_findings.extend(scan_file(f))

    block_severities = {"CRITICAL", "HIGH"} if args.strict else {"CRITICAL"}
    blocking = [f for f in all_findings if f["severity"] in block_severities]
    warnings = [f for f in all_findings if f["severity"] not in block_severities]

    # --- Report ---
    if warnings:
        print(f"\n⚠  WARNINGS ({len(warnings)}):")
        for f in warnings:
            print(f"  [{f['severity']}] {f['file']}:{f['line']}  {f['rule']}")
            print(f"    {f['snippet']}")

    if blocking:
        print(f"\n🚫 BLOCKED — {len(blocking)} secret(s) found:\n")
        for f in blocking:
            print(f"  [{f['severity']}] {f['file']}:{f['line']}  {f['rule']}")
            print(f"  └─ {f['description']}")
            print(f"     {f['snippet']}\n")
        print("Fix the issues above, then re-push.")
        print("To skip (emergencies only): git push --no-verify")
        sys.exit(1)

    scanned = len(files)
    total = len(all_findings)
    print(f"scan_secrets: ✓ {scanned} file(s) scanned, {total} warning(s), 0 blockers.")
    sys.exit(0)


if __name__ == "__main__":
    main()
