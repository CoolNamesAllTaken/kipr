// Entry of pcba3d.bundle.js, the classic-script build for reports opened from disk (file://,
// where browsers refuse ES modules): the project viewer loads it with a plain <script> and
// finds the module on window.KIPR_PCBA3D. Built by build_offline.mjs.
import { mountPcba3d } from './index.js';

window.KIPR_PCBA3D = { mountPcba3d };
