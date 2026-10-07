# wasm-gerber-renderer (vendored)

MIT, © dsafdsaf132 (see LICENSE). Upstream https://github.com/dsafdsaf132/wasm-gerber-viewer, via our fork
https://github.com/CoolNamesAllTaken/wasm-gerber-viewer at `92976b5a4b2cf5b42b1e2a068cce94dd8bc9e355` (FORK_COMMIT).
**Generated; do not edit**: refresh with `bash scripts/sync-fork.sh`, then `bash scripts/build-wasm.sh`.

- `core/`: `packages/wasm-gerber-renderer/{index.js, shared.js, index.d.ts}`; shared.js's `./drills.js` import points at
  `src/gerber/drills.js`.
- `core/wasm/`: `wasm_gerber_processor.js` + `_bg.wasm` built from `crate/` by scripts/build-wasm.sh;
  BUILD.json records the source hash and toolchain (CI checks it).
- `crate/`: the fork's `wasm/` Rust crate (wasm_gerber_processor) and `rust-toolchain.toml`.
