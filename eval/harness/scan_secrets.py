# SPDX-License-Identifier: Apache-2.0
"""Scan a directory tree for secrets and private data before it is published: AWS keys and account ids, private keys,
API tokens, e-mail addresses, IPv4 addresses outside libvirt's 192.168.122.0/24 and loopback, AWS host names.
Usage: python3 -I harness/scan_secrets.py <dir>"""
import collections, os, re, sys
root = sys.argv[1]
pats = {
    "aws_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_secret_words": re.compile(r"aws_secret_access_key|aws_session_token|SecretAccessKey|SessionToken|x-amz-security-token", re.I),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "tokens": re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|Bearer [A-Za-z0-9._-]{20,}"),
    "arn_account": re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:"),
    "account_word": re.compile(r"(?i)account[ _-]?id\W{0,5}\d{12}|\b\d{12}\b(?=.{0,40}(?i:account))"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "ipv4": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "aws_host": re.compile(r"\bip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}\b|\b[a-z0-9-]+\.compute(?:-1)?\.(?:internal|amazonaws\.com)\b"),
}
# Words that must not be published (names, domains); given at run time so that they are not stored here.
words = [w.strip() for w in os.environ.get("SCAN_PRIVATE_WORDS", "").split(",") if w.strip()]
if words:
    pats["private_words"] = re.compile("|".join(map(re.escape, words)), re.I)
hits = collections.defaultdict(collections.Counter)
where = collections.defaultdict(set)
for dp, dn, fn in os.walk(root):
    if ".git" in dp.split(os.sep):
        continue
    for f in fn:
        p = os.path.join(dp, f)
        if os.path.abspath(p) == os.path.abspath(__file__) or f.endswith((".xz", ".gz", ".zip")):
            continue  # this file's own patterns; compressed archives are scanned unpacked
        try:
            text = open(p, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        for k, rx in pats.items():
            for m in rx.findall(text):
                v = m if isinstance(m, str) else m[0]
                if k == "ipv4" and (v.startswith(("192.168.122.", "127.", "0.")) or v in ("255.255.255.0",)):
                    continue
                hits[k][v] += 1
                if len(where[(k, v)]) < 3:
                    where[(k, v)].add(os.path.relpath(p, root))
for k in pats:
    if hits[k]:
        print(f"== {k}: {sum(hits[k].values())} hits, {len(hits[k])} distinct")
        for v, n in hits[k].most_common(12):
            print(f"   {n:5} {v[:80]!r}  e.g. {sorted(where[(k, v)])[:2]}")
