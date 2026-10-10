#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copy runner/*.py into the two <script type="text/plain"> blocks of index.html.

The tool exports Python by handing the browser the text of those blocks, so the
copy inside index.html has to be byte-identical to the file in runner/. Editing
one and forgetting the other ships a config the exported runner cannot run, so
this script is the only way either copy should be changed.

    python3 tools/embed_python.py          # embed, then report
    python3 tools/embed_python.py --check   # verify only, exit 1 on a mismatch
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "index.html")
BLOCKS = [("runnerSrc", os.path.join(ROOT, "runner", "tesr_vision_runner.py")),
          ("lineSrc", os.path.join(ROOT, "runner", "tesr_line_engine.py"))]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    check = "--check" in sys.argv
    page = read(PAGE)
    changed, bad = [], []

    for ident, src_path in BLOCKS:
        src = read(src_path)
        # A <script> block is a raw-text element: entities are NOT decoded, so the
        # Python goes in verbatim and textContent hands it back unchanged. The one
        # thing that would break it is a literal </script inside the source.
        if "</script" in src:
            bad.append("%s: the source contains '</script' and cannot be embedded"
                       % ident)
            continue
        pat = re.compile(r'(<script type="text/plain" id="%s">)(.*?)(</script>)'
                         % ident, re.S)
        m = pat.search(page)
        if not m:
            bad.append("%s: block not found in index.html" % ident)
            continue
        if m.group(2) == src:
            print("%-12s in sync   (%d bytes)" % (ident, len(src)))
            continue
        if check:
            bad.append("%s: index.html copy differs from %s"
                       % (ident, os.path.relpath(src_path, ROOT)))
            continue
        page = page[:m.start()] + m.group(1) + src + m.group(3) + page[m.end():]
        changed.append(ident)

    if bad:
        for b in bad:
            print("MISMATCH  " + b)
        return 1
    if changed:
        with open(PAGE, "w", encoding="utf-8") as f:
            f.write(page)
        print("embedded: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
