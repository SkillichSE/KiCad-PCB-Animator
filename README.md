# pcb_timelapse


<img width="480" height="270" alt="soma" src="https://github.com/user-attachments/assets/3a769852-3ff8-4202-aeed-cdd726d28309" />


Builds a human-looking "routing timelapse" video from a finished `.kicad_pcb`.

## Requirements

- KiCad 9+ for the 3D spin (the 2D part works on older versions too) (the script must be run with KiCad's own Python, it provides `pcbnew`)
- `kicad-cli` (ships with KiCad)
- `ffmpeg` 4.3+ in PATH (needs the `xfade` filter)
- one SVG to PNG converter: `cairosvg`, `rsvg-convert`, `inkscape` or ImageMagick (`magick`)

## Run

Windows:

```
"D:\KiCad\bin\python.exe" pcb_timelapse_v4.py "Soma.kicad_pcb" -o soma.mp4
```

Linux / macOS:

```
python3 pcb_timelapse_v4.py board.kicad_pcb -o board.mp4
```

## Useful options

| Option | Default | Meaning |
|---|---|---|
| `-o`, `--output` | `<pcb>_timelapse.mp4` | output file |
| `--duration` | 60 | approximate video length, seconds |
| `--hold` | 3 | hold on the final frame, seconds |
| `--steps` | 220 | render steps (more = smoother, slower) |
| `--zoom` | 2.8 | base camera zoom |
| `--overview-every` | 16 | pull back to the overview every N steps on average |
| `--drift` | 0 | camera hand shake, try 0.002-0.004 |
| `--text-steps` | 90 | total typing steps for board text, texts are typed one by one |
| `--seed` | random | fixed seed for a repeatable result |
| `--no-zones` | off | skip copper pours |
| `--fps` | 30 | frames per second |
| `--width` | 1920 | video width |
| `--board-color` | green | board color in the final 3D spin: green, red, blue, black, white, yellow, purple |
| `--spin-seconds` | 6 | length of the final 360 degree 3D spin, 0 disables it |
| `--spin-frames` | 120 | 3D frames rendered for the spin (more = smoother, slower) |
| `--spin-tilt` | 35 | camera tilt during the spin, degrees |
| `--spin-zoom` | 1.0 | camera zoom during the spin |
| `--spin-quality` | basic | 3D render quality: basic or high |
| `--layers` | `B.Cu,F.Cu,B.Silkscreen,F.Silkscreen,Edge.Cuts` | drawn layers, in drawing order |
| `--theme` | KiCad default | KiCad color theme name |
| `--keep` | off | keep temp files |

## Notes

- Keep `B.Cu` before `F.Cu` in `--layers`, otherwise the bottom pour covers the top layer.
- A fast test run: `--steps 60 --duration 20 --width 1280 --seed 1`.
- The 3D spin uses `kicad-cli pcb render`; if it fails, the video is still produced without the spin.
- Rendering takes a while: every step is a separate `kicad-cli` export.

https://github.com/user-attachments/assets/fd2a5344-ad6f-4221-9a66-6f0b68b4ddc0

