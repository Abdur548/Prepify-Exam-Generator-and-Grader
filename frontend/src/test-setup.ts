/**
 * jsdom has no `PointerEvent`.
 *
 * Testing-library falls back to a generic event whose `clientX`/`clientY` are
 * `undefined`, so a "drag" arrives at the component with NaN deltas — which
 * silently passes every threshold, because comparisons against NaN are false.
 * The first run of the ProvenanceCard tests reported that a 3px press started a
 * drag and a 280px one did not move at all: both were the same NaN.
 *
 * `MouseEvent` does carry coordinates in jsdom, so a subclass is enough.
 */
class PointerEventPolyfill extends MouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;
  readonly isPrimary: boolean;

  constructor(type: string, params: PointerEventInit = {}) {
    super(type, params);
    this.pointerId = params.pointerId ?? 1;
    this.pointerType = params.pointerType ?? "mouse";
    this.isPrimary = params.isPrimary ?? true;
  }
}

if (!("PointerEvent" in globalThis)) {
  globalThis.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
}

// Pointer capture is not implemented in jsdom either. The component already
// tolerates it throwing, but stubbing keeps the tests about the gesture.
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.hasPointerCapture = () => false;
}
