#!/bin/bash
# Refresh web/project/vendor/wasm-gerber-renderer/ from our fork of wasm-gerber-viewer.
#
#     bash web/project/scripts/sync_vendored_renderer.bash [FORK_CHECKOUT] [REF]
#
# FORK_CHECKOUT defaults to $WGV or ../wasm-gerber-viewer next to this repo; REF (default: the
# checkout's HEAD) is exported with `git archive`, so the checkout's working tree is not touched.
# The JavaScript half (index.js, shared.js, index.d.ts and any extra ES modules the package ships,
# e.g. diff.js / board.js) comes from REF. The wasm half needs a Rust toolchain: with `wasm-pack`
# it is built from REF; without it the published npm release matching package.json's version is
# used (`npm pack`), which is what the fork's JS-only additions are written against. The README
# written here records both. The vendored directory is build output: do not edit it by hand.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
repo_root=$(cd "$here/../.." && pwd)
fork=${1:-${WGV:-$repo_root/../wasm-gerber-viewer}}
ref=${2:-HEAD}
target="$here/vendor/wasm-gerber-renderer"
pkg=packages/wasm-gerber-renderer

git -C "$fork" rev-parse --verify "$ref^{commit}" >/dev/null
commit=$(git -C "$fork" rev-parse --short "$ref^{commit}")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -C "$fork" archive "$ref" "$pkg" wasm | tar -x -C "$tmp"
src="$tmp/$pkg"
version=$(sed -n 's/^  "version": "\(.*\)",$/\1/p' "$src/package.json")

rm -rf "$target.new"
mkdir -p "$target.new/wasm"
cp "$src/LICENSE" "$src/index.d.ts" "$target.new/"
# every browser ES module of the package except the Node entry point
for f in "$src"/*.js; do
    case "$(basename "$f")" in node.js) continue ;; esac
    cp "$f" "$target.new/"
done

if command -v wasm-pack >/dev/null 2>&1; then
    (cd "$tmp" && wasm-pack build wasm --target web --out-dir pkg --release)
    cp "$tmp/wasm/pkg/wasm_gerber_processor.js" "$tmp/wasm/pkg/wasm_gerber_processor_bg.wasm" "$target.new/wasm/"
    wasm_note="built with wasm-pack from fork commit $commit"
else
    (cd "$tmp" && npm pack --silent "wasm-gerber-renderer@$version" >/dev/null && tar -xzf "wasm-gerber-renderer-$version.tgz")
    cp "$tmp/package/wasm/wasm_gerber_processor.js" "$tmp/package/wasm/wasm_gerber_processor_bg.wasm" "$target.new/wasm/"
    wasm_note="the published npm release wasm-gerber-renderer@$version (wasm-pack not installed here)"
fi

cat > "$target.new/README.md" <<README
# wasm-gerber-renderer $version, vendored from our fork

MIT (see LICENSE). Upstream: https://github.com/dsafdsaf132/wasm-gerber-viewer. Ours:
https://github.com/CoolNamesAllTaken/wasm-gerber-viewer, which adds the exported view math
(\`calculateFitView\`, \`projectToCanvas\`, ...) and \`renderInvertedLayer\` the layout view uses.

**Generated; do not edit.** Refresh with \`bash web/project/scripts/sync_vendored_renderer.bash\`.

- JavaScript: fork commit \`$commit\` (\`$ref\`), \`$pkg/*.js\` minus \`node.js\`.
- wasm: $wasm_note.

The layout view passes the .wasm URL explicitly (\`wasmInitInput\`) and never uses \`fit\`: every
render frames an explicit KiCad-mm box so all layers and both commits land on the same pixels.
README
rm -rf "$target"
mv "$target.new" "$target"
echo "synced wasm-gerber-renderer $version: JS from $commit; wasm: $wasm_note"
