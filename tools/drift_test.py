"""Measure head-tracking drift: keep the headset completely still while this runs."""
import math
import sys
import time

sys.path.insert(0, __file__.rsplit("\\tools", 1)[0])
from psvrscreen.psvr import PSVRSensors
from psvrscreen.tracking import HeadTracker, q_rotate, yaw_pitch

seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
tracker = HeadTracker()
raw = {"gyro": [0.0, 0.0, 0.0], "accel": [0.0, 0.0, 0.0], "n": 0}


def on_sample(s):
    raw["n"] += 1
    for i in range(3):
        raw["gyro"][i] += s.gyro[i]
        raw["accel"][i] += s.accel[i]
    tracker.add_sample(s)


sensors = PSVRSensors(on_sample)
sensors.start()
while not tracker.calibrated:
    time.sleep(0.05)
print("bias (rad/s):", [round(b, 5) for b in tracker._bias])
for t in range(seconds):
    time.sleep(1)
    q = tracker.orientation()
    yaw, pitch = yaw_pitch(q)
    right = q_rotate(q, (1.0, 0.0, 0.0))
    roll = math.degrees(math.asin(max(-1.0, min(1.0, right[1]))))
    n = raw["n"] or 1
    g = [round(v / n, 4) for v in raw["gyro"]]
    a = [round(v / n, 2) for v in raw["accel"]]
    raw.update(gyro=[0.0, 0.0, 0.0], accel=[0.0, 0.0, 0.0], n=0)
    print(f"{t + 1:2d}s yaw {math.degrees(yaw):7.2f}  pitch {math.degrees(pitch):7.2f}  roll {roll:7.2f}"
          f"  | gyro avg {g}  accel avg {a}", flush=True)
sensors.stop()
