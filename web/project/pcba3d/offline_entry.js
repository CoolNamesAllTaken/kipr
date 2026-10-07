// Entry of pcba3d.bundle.js, the classic-script build for reports opened from disk (file://,
// where browsers refuse ES modules): the project viewer loads it with a plain <script> and
// finds the module on window.KIPR_PCBA3D. Built by build_offline.mjs.
// It also hands the project viewer's layout tab the gerber renderer (boarddd/gerber, the same
// vendored copy the 3D module uses) and the wasm-bindgen glue, so gerbers render from disk too:
// window.KIPR_GERBER.
import { mountPcba3d } from './index.js';
import * as gerber from '../vendor/boarddd/src/gerber/index.js';
import * as wasmGlue from '../vendor/boarddd/third_party/wasm-gerber-renderer/core/wasm/wasm_gerber_processor.js';

window.KIPR_PCBA3D = { mountPcba3d };
window.KIPR_GERBER = { gerber, wasmGlue };
