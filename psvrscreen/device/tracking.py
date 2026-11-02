"""Head orientation from the PSVR gyro + accelerometer (Mahony filter).

The gyro gives fast, smooth rotation; gravity from the accelerometer slowly corrects tilt so "up" stays up.
There is no compass, so left/right (yaw) can drift slowly: recenter fixes it.
Quaternions are (w, x, y, z), rotating head (sensor) space into world space. World: +y up, -z forward.
"""
import math
import threading

GRAVITY = 9.81
TICK_SECONDS = 1e-6         # sensor timestamps are 1 MHz, 24-bit
NOMINAL_DT = 0.0005         # 2000 samples per second
KP = 1.5                    # how fast gravity corrects tilt
STILL_BLOCK = 200           # samples (0.1 s) checked at a time for "the headset is not moving"
STILL_SPREAD = 0.03         # rad/s: gyro spread within a block below this = still
MAX_BIAS = 0.1              # rad/s: a real gyro zero offset is tiny; anything bigger means it was moving
START_TIMEOUT = 4000        # samples (2 s): start anyway if the headset never holds still


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def q_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def q_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def q_rotate(q, v):
    return q_mul(q_mul(q, (0.0, v[0], v[1], v[2])), q_conj(q))[1:]


def q_axis_angle(axis, angle):
    s = math.sin(angle / 2)
    return (math.cos(angle / 2), axis[0] * s, axis[1] * s, axis[2] * s)


def q_to_matrix(q):
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def yaw_pitch(q):
    fx, fy, fz = q_rotate(q, (0.0, 0.0, -1.0))
    return math.atan2(-fx, -fz), math.asin(max(-1.0, min(1.0, fy)))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


class HeadTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._q = (1.0, 0.0, 0.0, 0.0)
        self._yaw_ref = (1.0, 0.0, 0.0, 0.0)
        self._bias = [0.0, 0.0, 0.0]
        self._last_tick = None
        self._block = []            # recent (gyro, accel) samples for stillness detection
        self._samples_seen = 0
        self.calibrated = False

    def add_sample(self, sample):
        """Called ~2000 times per second from the sensor thread."""
        dt = NOMINAL_DT
        if self._last_tick is not None:
            d = ((sample.tick - self._last_tick) & 0xFFFFFF) * TICK_SECONDS
            if 0.000475 <= d <= 0.000525:
                dt = d
        self._last_tick = sample.tick
        gyro, accel = sample.gyro, sample.accel
        self._samples_seen += 1
        self._block.append((gyro, accel))
        if len(self._block) >= STILL_BLOCK:
            self._check_still()

        if not self.calibrated:
            return

        w = [gyro[i] - self._bias[i] for i in range(3)]
        a_len = _length(accel)
        if 0.85 * GRAVITY < a_len < 1.15 * GRAVITY:
            a = (accel[0] / a_len, accel[1] / a_len, accel[2] / a_len)
            with self._lock:
                q = self._q
            v = q_rotate(q_conj(q), (0.0, 1.0, 0.0))   # where "up" should be, seen from the head
            e = _cross(a, v)
            w = [w[i] + KP * e[i] for i in range(3)]

        angle = _length(w) * dt
        if angle > 0.0:
            step = q_axis_angle([c * dt / angle for c in w], angle)
            with self._lock:
                self._q = q_normalize(q_mul(self._q, step))

    def _check_still(self):
        """Every 0.1 s: if the gyro barely changed, the headset is still, so its average is the zero offset."""
        block, self._block = self._block, []
        n = len(block)
        mean = [sum(g[i] for g, _ in block) / n for i in range(3)]
        spread = max(max(g[i] for g, _ in block) - min(g[i] for g, _ in block) for i in range(3))
        still = spread < STILL_SPREAD and all(abs(m) < MAX_BIAS for m in mean)
        if not self.calibrated:
            if still or self._samples_seen >= START_TIMEOUT:
                if still:
                    self._bias = mean
                accel = [sum(a[i] for _, a in block) / n for i in range(3)]
                self._start(accel)
            return
        if still:
            # Learn quickly: a wrong zero offset makes the view spin.
            self._bias = [self._bias[i] + 0.3 * (mean[i] - self._bias[i]) for i in range(3)]

    def _start(self, a):
        a_len = _length(a) or 1.0
        a = [c / a_len for c in a]
        axis = _cross(a, (0.0, 1.0, 0.0))
        s = _length(axis)
        angle = math.atan2(s, a[1])
        with self._lock:
            self._q = q_axis_angle([c / s for c in axis], angle) if s > 1e-6 else (1.0, 0.0, 0.0, 0.0)
        self.calibrated = True
        self.recenter()

    def recenter(self):
        """Make the current looking direction 'straight ahead'."""
        with self._lock:
            yaw, _ = yaw_pitch(self._q)
            self._yaw_ref = q_axis_angle((0.0, 1.0, 0.0), yaw)

    def orientation(self):
        with self._lock:
            return q_mul(q_conj(self._yaw_ref), self._q)
