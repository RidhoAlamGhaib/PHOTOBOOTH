"""Try stickers on your own photo before putting them in the booth.

    python tools/test_sticker.py foto.jpg              # every sticker in stickers.json
    python tools/test_sticker.py foto.jpg pita kucing  # only these

Writes sticker_test.jpg next to the photo: one tile per sticker, with the face
points the booth found (green = eyes, blue = nose, red = mouth). Edit
stickers/stickers.json (width / dy / dx) and run it again until it sits right.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stickers  # noqa: E402
from effects import detect_faces  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    img = cv2.imread(path)
    if img is None:
        print(f"Cannot read {path}")
        return 1
    stickers._catalog = None                          # re-read stickers.json every run
    wanted = sys.argv[2:] or [s["id"] for s in stickers.catalog()]
    faces = detect_faces(img)
    print(f"{len(faces)} face(s) found")
    if not faces:
        print("No face: try a brighter photo, facing the camera.")
    marked = img.copy()
    for f in faces:
        r = max(2, int(f["box"][2] * 0.035))          # dot size follows the face
        for p in f["eyes"]:
            cv2.circle(marked, (int(p[0]), int(p[1])), r, (0, 200, 0), -1)
        if f.get("nose"):
            cv2.circle(marked, (int(f["nose"][0]), int(f["nose"][1])), r, (255, 120, 0), -1)
        for p in f.get("mouth") or []:
            cv2.circle(marked, (int(p[0]), int(p[1])), r, (0, 0, 255), -1)
    tiles = [("titik wajah", marked)]
    for sid in wanted:
        if stickers.find(sid) is None:
            print(f"Sticker '{sid}' not in stickers.json (or its PNG is missing)")
            continue
        tiles.append((sid, stickers.draw(img, sid, faces)))
    h = 420
    row = []
    for name, t in tiles:
        t = cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h))
        cv2.putText(t, name, (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)
        cv2.putText(t, name, (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 40), 1)
        row.append(t)
    cols = 3
    rows = [row[i:i + cols] for i in range(0, len(row), cols)]
    width = max(sum(t.shape[1] for t in r) for r in rows)
    sheet = np.vstack([np.hstack(r + [np.full((h, width - sum(t.shape[1] for t in r), 3), 255, np.uint8)])
                       for r in rows])
    out = os.path.join(os.path.dirname(os.path.abspath(path)), "sticker_test.jpg")
    cv2.imwrite(out, sheet)
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
