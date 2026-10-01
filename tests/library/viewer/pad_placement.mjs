// Where the 3D viewer draws each pad's copper and hole, and where it puts each model's origin.
//     node pad_placement.mjs <file with {name: {geom, models}} JSON>   -> prints {name: {pads, models}} JSON
// Used by tests/library/test_pad_placement.py, which compares the result with KiCad's own (golden.json).
import { readFileSync } from 'node:fs';
import { applyMatrix, modelMatrix, padHoleCenter, padDrill, padOutline, padToPcb } from '../../../web/library/js/kicad3d.js';

const bbox = (pts) => {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
};
const out = {};
for (const [name, { geom, models }] of Object.entries(JSON.parse(readFileSync(process.argv[2], 'utf8')))) {
  out[name] = {
    pads: geom.pads.map((pad) => {
      const { outer, extra } = padOutline(pad, 32);
      const local = bbox(outer); // the pad's own shape, before rotation (custom pads: the anchor)
      return {
        number: pad.number,
        copper_center: padToPcb(pad, [(local[0] + local[2]) / 2, (local[1] + local[3]) / 2]),
        copper_bbox: bbox([outer, ...extra].flat().map((q) => padToPcb(pad, q))),
        hole_center: padDrill(pad) ? padHoleCenter(pad) : null,
      };
    }),
    // board frame (y up): the model's own origin lands at the KiCad offset
    models: models.map((m) => applyMatrix(modelMatrix(m), [0, 0, 0])),
  };
}
process.stdout.write(JSON.stringify(out));
