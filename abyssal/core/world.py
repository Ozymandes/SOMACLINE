"""Logical world definition. The simulation lives here and NEVER sees pixels.

CONTRACT (binding on every module):
  * The organism is authored in a fixed square world of WORLD_SIZE x WORLD_SIZE
    units, centred on (0, 0). Valid coordinates are [-WORLD_HALF, +WORLD_HALF].
  * WORLD_RADIUS is the design radius: the organism's outermost reach at full
    expansion. Everything must fit inside it so nothing ever clips.
  * Nothing in organism/ or telemetry/ may import pixels, widget sizes, cairo,
    or GTK. Conversion happens in exactly one place: core.viewport.Viewport.
"""

WORLD_SIZE = 1000.0
WORLD_HALF = WORLD_SIZE / 2.0

# The organism must stay within this radius so it never touches the frame.
WORLD_RADIUS = 460.0
