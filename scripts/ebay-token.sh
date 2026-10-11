#!/usr/bin/env bash
# Mint an eBay application token (client credentials grant) and print it.
# App tokens expire after a couple of hours, so mint a fresh one each run:
#
#   EBAY_TOKEN=$(scripts/ebay-token.sh) python -m soldshadow sweep
#
# Needs EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (your production keyset's App ID and Cert ID).
set -euo pipefail
: "${EBAY_CLIENT_ID:?set EBAY_CLIENT_ID (your keyset App ID)}"
: "${EBAY_CLIENT_SECRET:?set EBAY_CLIENT_SECRET (your keyset Cert ID)}"
url=${EBAY_TOKEN_URL:-https://api.ebay.com/identity/v1/oauth2/token}
resp=$(curl -sS --max-time 30 -u "$EBAY_CLIENT_ID:$EBAY_CLIENT_SECRET" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  --data-urlencode "scope=https://api.ebay.com/oauth/api_scope" "$url")
python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    sys.exit("eBay token endpoint did not return JSON: " + sys.argv[1][:200])
if "access_token" not in d:
    sys.exit("no access_token from eBay: " + json.dumps(d)[:300])
print(d["access_token"])' "$resp"
