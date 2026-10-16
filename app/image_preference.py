"""Locate the image comparison's Skip control using two visual anchors."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def templates():
    import cv2
    import numpy as np
    from PIL import Image
    base = Path(__file__).resolve().parent / 'assets'
    return tuple(cv2.cvtColor(np.array(Image.open(base / name).convert('RGB')),
                              cv2.COLOR_RGB2GRAY)
                 for name in ('preference-title.png', 'preference-skip.png'))


def skip_point(screenshot):
    """Return client-relative coordinates only when the title and Skip both match."""
    import cv2
    import numpy as np
    frame = cv2.GaussianBlur(cv2.cvtColor(np.array(screenshot.convert('RGB')),
                                        cv2.COLOR_RGB2GRAY), (3, 3), .8)
    title, skip = templates()
    height, width = frame.shape
    for scale in (.75, 1, 1.25, 1.5, 1.75, 2, 2.5):
        # Light smoothing tolerates subpixel text antialiasing at Windows scales.
        heading = cv2.GaussianBlur(cv2.resize(title, None, fx=scale, fy=scale), (3, 3), .8)
        button = cv2.GaussianBlur(cv2.resize(skip, None, fx=scale, fy=scale), (3, 3), .8)
        th, tw = heading.shape
        bh, bw = button.shape
        if th > height or tw + bw > width:
            continue
        for inverted in (False, True):
            anchor = 255 - heading if inverted else heading
            _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(frame, anchor, cv2.TM_CCOEFF_NORMED))
            if score < .90:
                continue
            x, y = location
            # Skip is on the same heading row, to its right. Never click a bare
            # "Skip" found elsewhere in the app or choose either image.
            left = x + tw + int(30 * scale)
            top, bottom = max(0, y - int(8 * scale)), min(height, y + th + int(8 * scale))
            strip = frame[top:bottom, left:]
            if strip.shape[0] < bh or strip.shape[1] < bw:
                continue
            needle = 255 - button if inverted else button
            _, score, _, position = cv2.minMaxLoc(cv2.matchTemplate(strip, needle, cv2.TM_CCOEFF_NORMED))
            if score >= .88:
                return left + position[0] + bw // 2, top + position[1] + bh // 2
    return None
