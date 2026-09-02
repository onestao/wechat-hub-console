"""Contrast audit for the Console v2 palette.

Run:  python design_v2/qa/contrast_audit.py
Exits non-zero if any pair used for text falls below WCAG AA (4.5:1).

Values here mirror css/tokens.css. When a token changes, update this table in
the same commit — it is the only place the palette's readability is enforced.
"""

from __future__ import annotations

import sys


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    hex_value = color.lstrip("#")
    r, g, b = (int(hex_value[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(foreground: str, background: str) -> float:
    a, b = _luminance(foreground), _luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


# (label, foreground, background)
TEXT_PAIRS = [
    # --- Light -------------------------------------------------------------
    ("L primary / page", "#1f2329", "#f5f6f7"),
    ("L primary / surface", "#1f2329", "#ffffff"),
    ("L secondary / page", "#646c76", "#f5f6f7"),
    ("L secondary / surface", "#646c76", "#ffffff"),
    ("L secondary / subtle", "#646c76", "#fafbfc"),
    ("L brand-text / page", "#04814a", "#f5f6f7"),
    ("L brand-text / surface", "#04814a", "#ffffff"),
    ("L brand-text / soft", "#04814a", "#e9f8f0"),
    ("L warning-text / page", "#9a6412", "#f5f6f7"),
    ("L warning-text / soft", "#9a6412", "#fdf4e5"),
    ("L danger-text / page", "#b33737", "#f5f6f7"),
    ("L danger-text / soft", "#b33737", "#fcedec"),
    ("L info-text / page", "#2c5cb4", "#f5f6f7"),
    ("L info-text / soft", "#2c5cb4", "#eef4fe"),
    ("L login-ink / plate", "#646c76", "#ffffff"),
    # --- Dark --------------------------------------------------------------
    ("D primary / page", "#e9ecf0", "#16181c"),
    ("D primary / surface", "#e9ecf0", "#1f2227"),
    ("D secondary / page", "#9aa2ad", "#16181c"),
    ("D secondary / surface", "#9aa2ad", "#1f2227"),
    ("D secondary / subtle", "#9aa2ad", "#24272d"),
    ("D tertiary / surface", "#828a95", "#1f2227"),
    ("D brand-text / surface", "#4fd894", "#1f2227"),
    ("D brand-text / soft", "#4fd894", "#1b2c23"),
    ("D warning-text / soft", "#e8b45c", "#2c2418"),
    ("D danger-text / soft", "#f09a9a", "#2e2020"),
    ("D info-text / soft", "#96b8f5", "#1d2635"),
    ("D on-brand / brand fill", "#10251a", "#2ecc7a"),
    ("D on-brand / brand hover", "#10251a", "#3ad98a"),
    # --- prefers-contrast: more (light) ------------------------------------
    ("HC-L on-brand / fill", "#ffffff", "#04803f"),
    ("HC-L on-brand / hover", "#ffffff", "#036b35"),
    ("HC-L secondary / page", "#565d66", "#f5f6f7"),
]

# Pairs deliberately allowed below 4.5:1, with the reason each is safe.
ACCEPTED = [
    (
        "L tertiary / page",
        "#868e98",
        "#f5f6f7",
        "incidental captions only; never the sole carrier of meaning",
    ),
    (
        "L white on brand fill",
        "#ffffff",
        "#07c160",
        "WeChat brand green is kept for identity on the filled primary button; "
        "prefers-contrast: more swaps in #04803f (5.0:1)",
    ),
]

AA = 4.5


def main() -> int:
    failures = []
    print(f"WCAG AA target for text: {AA}:1\n")
    for label, foreground, background in TEXT_PAIRS:
        ratio = contrast(foreground, background)
        ok = ratio >= AA
        if not ok:
            failures.append((label, round(ratio, 2)))
        print(f"{'ok  ' if ok else 'FAIL'} {label:26} {ratio:5.2f}")

    print("\nAccepted exceptions (documented, not text-critical):")
    for label, foreground, background, reason in ACCEPTED:
        print(f"     {label:26} {contrast(foreground, background):5.2f}  — {reason}")

    if failures:
        print(f"\n{len(failures)} pair(s) below AA:")
        for label, ratio in failures:
            print(f"  {label}: {ratio}")
        return 1
    print(f"\nAll {len(TEXT_PAIRS)} text pairs pass AA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
