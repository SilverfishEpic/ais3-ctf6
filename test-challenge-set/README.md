# Test Challenge Set

20 real, publicly-released CTF challenges — **4 categories × 5 difficulties** — each shipped
with its original challenge files *and* an official solution/writeup. Used as fixture input
for exercising Memory Core, the Agent Bridge, and the category templates in `templates/`.

## Layout

```text
test-challenge-set/<category>/<CTFevent>-<challenge-name>/<challenge files>
```

Each challenge directory is an unmodified copy of its upstream directory, typically:

| Entry                    | Contents                                             |
| ------------------------ | ---------------------------------------------------- |
| `README.md`              | challenge description, author, difficulty, solves     |
| `dist/` or `challenge/`  | files handed to players (binaries, source, Dockerfile) |
| `solution/`              | official writeup and solve script                     |

`MANIFEST.json` carries the same table in machine-readable form.

## Selection

All 20 come from **SekaiCTF 2022–2026** (`github.com/project-sekai-ctf`). SekaiCTF is the
only long-running open-source CTF whose repos publish a *native* 5-tier difficulty label
alongside official solutions for every challenge, so no cross-event difficulty guessing
is involved.

Difficulty names map to the upstream tiers as follows:

| Here     | SekaiCTF     |
| -------- | ------------ |
| `easy`   | `Easy (1)`   |
| `medium` | `Normal (2)` |
| `hard`   | `Hard (3)`   |
| `insane` | `Expert (4)` |
| `master` | `Master (5)` |

A challenge was eligible only if it has an official solution, a writeup `README.md`,
real challenge files committed in-repo (not just an off-site download link), and a
total size under 25 MB. That left 91 of 176 challenges.

The pick is a seeded random draw (`seed=20260726`) under one hard constraint:

> **Within a single CTF event, no category and no difficulty may repeat.**

With 5 events and 20 cells this forces every event to contribute exactly 4 challenges —
one per category, each at a different difficulty. The resulting assignment:

| Event         | crypto   | web      | pwn      | rev      |
| ------------- | -------- | -------- | -------- | -------- |
| SekaiCTF 2022 | `hard`   | `medium` | `insane` | `easy`   |
| SekaiCTF 2023 | `medium` | `master` | `easy`   | `insane` |
| SekaiCTF 2024 | `insane` | `hard`   | `medium` | `master` |
| SekaiCTF 2025 | `easy`   | `insane` | `master` | `hard`   |
| SekaiCTF 2026 | `master` | `easy`   | `hard`   | `medium` |

## Contents

| Category | Difficulty | Challenge | Event | Solves |
| -------- | ---------- | --------- | ----- | -----: |
| crypto | `easy` | [SSSS](crypto/SekaiCTF2025-ssss) | 2025 | 236 |
| crypto | `medium` | [cryptoGRAPHy 2](crypto/SekaiCTF2023-cryptography-2) | 2023 | 55 |
| crypto | `hard` | [Diffecient](crypto/SekaiCTF2022-diffecient) | 2022 | 7 |
| crypto | `insane` | [Squares vs. Cubes](crypto/SekaiCTF2024-squares-vs-cubes) | 2024 | 2 |
| crypto | `master` | [Iihash](crypto/SekaiCTF2026-iihash) | 2026 | 29 |
| web | `easy` | [Migurimental](web/SekaiCTF2026-migurimental) | 2026 | 156 |
| web | `medium` | [Sekai Game Start](web/SekaiCTF2022-sekai-game-start) | 2022 | 63 |
| web | `hard` | [lookup](web/SekaiCTF2024-lookup) | 2024 | 5 |
| web | `insane` | [rednote](web/SekaiCTF2025-rednote) | 2025 | 4 |
| web | `master` | [Leakless Note](web/SekaiCTF2023-leakless-note) | 2023 | 4 |
| pwn | `easy` | [Cosmic Ray](pwn/SekaiCTF2023-cosmic-ray) | 2023 | 149 |
| pwn | `medium` | [nolibc](pwn/SekaiCTF2024-nolibc) | 2024 | 56 |
| pwn | `hard` | [echo-chamber](pwn/SekaiCTF2026-echo-chamber) | 2026 | 6 |
| pwn | `insane` | [BFS](pwn/SekaiCTF2022-bfs) | 2022 | 2 |
| pwn | `master` | [vkfs](pwn/SekaiCTF2025-vkfs) | 2025 | 2 |
| rev | `easy` | [Matrix Lab 1](rev/SekaiCTF2022-matrix-lab-1) | 2022 | 191 |
| rev | `medium` | [Untitled Encore](rev/SekaiCTF2026-untitled-encore) | 2026 | 238 |
| rev | `hard` | [Miku Music Machine](rev/SekaiCTF2025-miku-music-machine) | 2025 | 30 |
| rev | `insane` | [Sahuang Flag Checker](rev/SekaiCTF2023-sahuang-flag-checker) | 2023 | 13 |
| rev | `master` | [Sekai Licensing](rev/SekaiCTF2024-sekai-licensing) | 2024 | 0 |

Total ≈ 57 MB, dominated by shipped `libc`/container artifacts in
`pwn/SekaiCTF2026-echo-chamber` (25 MB) and `web/SekaiCTF2024-lookup` (20 MB).

`rev/SekaiCTF2024-sekai-licensing` went unsolved during the event (0 solves); its
official solution is still published upstream, which is what eligibility required.

## Upstream and licensing

Source repositories: `https://github.com/project-sekai-ctf/sekaictf-<year>` for
2022 through 2026. Per-challenge upstream URLs are in `MANIFEST.json`.

Project Sekai dual-licenses these repositories: **code under AGPL-3.0**, **non-code
content (descriptions, writeups) under CC BY-NC-SA 4.0**. Files here are unmodified
copies and remain under those terms — keep the attribution above if this set is
redistributed, and note the non-commercial clause on the writeups.
