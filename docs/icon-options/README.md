# Icon options

Candidate app icons (SVG, 128×128). The one in use is `catflap/resources/catflap.svg`
(option **T1**). To switch, copy the chosen file over `catflap/resources/catflap.svg`;
the RPM, Flatpak and the app's windows all use that file. The tray icon is drawn
in code (`make_pixmap` in `catflap/ui_common.py`) in the same tunnel-and-cat shape,
so it needs updating separately if the shape changes.

| | File | Idea |
|---|---|---|
| A | `a-peek.svg` | A cat peeking through a cat flap (current) |
| B | `b-shield.svg` | The cat flap inside a security shield |
| C | `c-tunnel.svg` | A cat walking into a tunnel (the VPN tunnel) |
| D | `d-line.svg` | Flat, single-colour line art of A |
| E | `e-keyhole.svg` | A cat-eared padlock with a keyhole |
| F | `f-paw.svg` | A paw print on the cat flap |
| G | `g-night-eyes.svg` | Two glowing cat eyes in a dark cat flap |
| H | `h-house.svg` | A house whose door has a cat flap, with the cat peeking out |
| I | `i-network-cat.svg` | A cat head drawn as network nodes and links |
| J | `j-round-badge.svg` | Round badge with a cat in profile |
| K | `k-monogram.svg` | "cf" monogram with cat ears |
| L | `l-tail-key.svg` | A sitting cat whose tail ends in a key |
| M | `m-pixel.svg` | Pixel-art cat in a cat flap |
| N | `n-signal-ears.svg` | Signal waves with cat ears |
| O | `o-blue-peek.svg` | A in C's blue palette: white cat, light-blue frame |
| P | `p-sky-cat.svg` | A in C's blues with a light-blue cat and a white frame |
| Q | `q-tunnel-peek.svg` | C's tunnel arches as the cat flap, with A's cat peeking out |
| R | `r-night-blue.svg` | A on navy with a blue-gradient cat |

Note: Qt's SVG renderer (also used by KDE for icons) ignores `clipPath`, so
shapes must not rely on clipping to look right.
| T1 | `t1-tunnel-white-cat.svg` | Blue tunnel background with the first icon's cat, in white |
| T2 | `t2-tunnel-sky-cat.svg` | Same, with a soft sky-blue cat |
| T3 | `t3-tunnel-green-cat.svg` | Same, with the first icon's original green cat |
| X1 | `x1-paw-out.svg` | Blue tunnel; a white forearm and paw coming out towards you, toes over the edge |
| X2 | `x2-paw-swat.svg` | Blue tunnel; a white paw swatting out sideways, beans showing |
| X3 | `x3-paw-highfive.svg` | Blue tunnel; a white paw raised out of the tunnel, palm facing you |
| Y1 | `y1-paw-through-pink.svg` | Blue tunnel; a cartoon white paw pushing through towards you, pink toe beans |
| Y2 | `y2-paw-through-blue.svg` | Same, light-blue toe beans |
| Y3 | `y3-paw-big-pink.svg` | Bigger paw reaching further out past the tunnel edge, pink toe beans |
