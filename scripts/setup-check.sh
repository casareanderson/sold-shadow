#!/usr/bin/env bash
# Runs the steps in docs/SETUP.md that need no eBay keyset and no trawl.dev key.
# Used by .github/workflows/setup-check.yml. Run it locally from the repo root too.
set -euo pipefail
step() { printf '\n== %s\n' "$*"; }
db=$(mktemp -d)/watch.db

step "1. offline tests (nothing to install)"
python3 -m unittest 2>&1 | tail -3

step "1. CLI loads, every command has --help"
python3 -m soldshadow --help >/dev/null
for c in track sweep stats compare comps; do python3 -m soldshadow "$c" --help >/dev/null; done; echo ok

step "3. an empty corpus"
python3 -m soldshadow --db "$db" stats | tee stats.txt
grep -qE "tracked +0" stats.txt; rm -f stats.txt
python3 -m soldshadow --db "$db" comps "Casio fx-CG50" | grep -q "no sold comps yet for that query" && echo "comps: none yet"

step "3. missing keys and empty input give plain messages and exit 1"
expect() { local want=$1; shift; set +e; out=$("$@" 2>&1); rc=$?; set -e
  [ "$rc" -eq 1 ] && grep -q "$want" <<<"$out" && ! grep -q Traceback <<<"$out" || { echo "FAIL: $*: rc=$rc $out"; exit 1; }
  echo "ok: $want"; }
expect "EBAY_TOKEN is not set" env -u EBAY_TOKEN python3 -m soldshadow --db "$db" track "Casio fx-CG50"
expect "EBAY_TOKEN is not set" env -u EBAY_TOKEN python3 -m soldshadow --db "$db" sweep
expect "nothing to track" python3 -m soldshadow --db "$db" track
expect "TRAWL_KEY is not set" env -u TRAWL_KEY python3 -m soldshadow --db "$db" compare "Casio fx-CG50"

step "2. the token helper"
expect "set EBAY_CLIENT_ID" env -u EBAY_CLIENT_ID -u EBAY_CLIENT_SECRET bash scripts/ebay-token.sh
port=18095
python3 - "$port" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        ok = self.headers.get("Authorization", "").startswith("Basic ") and "grant_type=client_credentials" in body
        out = json.dumps({"access_token": "v^1.1#fake", "expires_in": 7200} if ok else {"error": "invalid_client"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
fake=$!; trap 'kill $fake 2>/dev/null || true' EXIT; sleep 1
tok=$(EBAY_CLIENT_ID=id EBAY_CLIENT_SECRET=secret EBAY_TOKEN_URL="http://127.0.0.1:$port" bash scripts/ebay-token.sh)
[ "$tok" = "v^1.1#fake" ] && echo "helper prints the token"

printf '\nSETUP CHECK PASSED\n'
