#!/usr/bin/env python3
"""Standalone Canon EDSDK smoke test — NO Qt, NO photobooth app.

Run this FIRST to confirm the camera + EDSDK bridge work on their own:

    python test_canon.py

What it does:
  * opens the camera, starts live view in an OpenCV window
  * press SPACE  -> fires the real shutter, saves a full-res JPEG here
  * press ESC/q  -> quit

If EDSDK.dll isn't found, set EDSDK_DLL_DIR first, e.g.:
    set EDSDK_DLL_DIR=C:\\path\\to\\EDSDK\\Dll
"""

import time
import logging

import cv2

from canon_edsdk import CanonCamera

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main():
    cam = CanonCamera()   # uses EDSDK_DLL_DIR or ./EDSDK/Dll
    print("Opening camera...")
    cam.open()
    cam.start_live_view()
    print("Live view started. SPACE = capture, ESC/q = quit.")

    shot_n = 0
    try:
        while True:
            frame = cam.grab_live_frame()
            if frame is not None:
                cv2.imshow("Canon live view (SPACE=capture, q=quit)", frame)
            key = cv2.waitKey(10) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord(" "):
                print("Capturing full-res shot...")
                t0 = time.time()
                try:
                    shot = cam.capture_still()
                    shot_n += 1
                    fname = f"canon_test_{shot_n:02d}.jpg"
                    cv2.imwrite(fname, shot)
                    print(f"  saved {fname}  {shot.shape[1]}x{shot.shape[0]}  "
                          f"in {time.time() - t0:.1f}s")
                except Exception as e:
                    print(f"  capture FAILED: {e}")
    finally:
        cv2.destroyAllWindows()
        cam.close()
        print("Closed.")


if __name__ == "__main__":
    main()
