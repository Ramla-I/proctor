#!/bin/sh
# Test package for 001_helloworld (component spec §2.3).
#   $1 = test_data directory, $2 = compiled artifact
set -eu
out=$("$2")
if [ "$out" != "Hello World!" ]; then
    echo "unexpected output: '$out'" >&2
    exit 1
fi
exit 0
