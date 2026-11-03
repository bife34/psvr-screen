"""Makes environments/Deep space.jpg: only stars all around you (the galaxy's sky) - a 360° picture, 4096 x 2048.

Stars are spread evenly over the sphere (more rows near the equator of the picture, as it stretches the poles),
each a soft dot (never one sharp pixel: those sparkle in a headset), most faint, a few bright with a glow, slightly blue / white / warm; a very faint band of the Milky Way.
Made here, so it is free to use (CC0) like the other surroundings. Run: python tools/make_deep_space.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

W, H = 4096, 2048
OUT = Path(__file__).resolve().parent.parent / "environments" / "Deep space.jpg"


def _blur(a, sigma):
    """Gaussian blur of an (h, w, 3) float array, wrapping around left / right like the sky does."""
    r = int(sigma * 3)
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma * sigma)); k /= k.sum()
    out = sum(w * np.roll(a, s, axis=1) for s, w in zip(range(-r, r + 1), k))
    return sum(w * np.roll(out, s, axis=0) for s, w in zip(range(-r, r + 1), k))


def main():
    rng = np.random.default_rng(2026)
    sky = np.zeros((H, W, 3), np.float32)

    # The Milky Way: a faint, uneven band across the sky, tilted
    lon = (np.arange(W) / W - 0.5) * 2 * np.pi
    lat = (0.5 - np.arange(H) / H) * np.pi
    lon, lat = np.meshgrid(lon, lat)
    d = np.stack([np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], -1)
    pole = np.array([0.35, 0.8, -0.48]); pole /= np.linalg.norm(pole)
    band = np.exp(-(d @ pole) ** 2 / 0.018)
    noise = Image.fromarray((rng.random((H // 16, W // 16)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    noise = np.asarray(noise.filter(ImageFilter.GaussianBlur(6)), np.float32) / 255
    glow = band * (0.35 + 0.9 * noise) * 0.07
    sky += glow[..., None] * np.array([0.75, 0.8, 1.0])

    # Stars: even over the sphere, brightness a power law (many faint, few bright)
    n = 26000
    z = rng.uniform(-1, 1, n)
    a = rng.uniform(-np.pi, np.pi, n)
    star_lat, star_lon = np.arcsin(z), a
    near_band = np.exp(-((np.stack([np.cos(star_lat) * np.sin(a), z, np.cos(star_lat) * np.cos(a)], -1)
                          @ pole) ** 2) / 0.03)
    keep = rng.random(n) < 0.55 + 0.45 * near_band          # more stars in the Milky Way
    star_lat, star_lon = star_lat[keep], star_lon[keep]
    m = len(star_lat)
    bright = np.clip(rng.pareto(2.2, m) * 0.16 + 0.05, 0, 3.0)
    tint = rng.choice(np.array([[0.75, 0.85, 1.0], [1.0, 1.0, 1.0], [1.0, 0.92, 0.78], [1.0, 0.8, 0.65]]),
                      m, p=[0.3, 0.45, 0.17, 0.08])
    x = ((star_lon / (2 * np.pi) + 0.5) * W).astype(int) % W
    y = np.clip(((0.5 - star_lat / np.pi) * H).astype(int), 0, H - 1)
    points = np.zeros_like(sky)
    np.add.at(points, (y, x), tint * bright[:, None])
    # Each star a soft dot a few pixels wide: one-pixel stars sparkle and jump between the headset's pixels with
    # every tiny head movement - the sky seemed to tremble
    sky += np.minimum(_blur(points, 1.1) * 9.0, 1.0)
    # A soft glow around the bright ones
    big = np.where(bright[:, None] > 0.6, tint * bright[:, None], 0)
    halo = np.zeros_like(sky)
    np.add.at(halo, (y, x), big)
    halo = _blur(halo, 2.2)
    sky += halo * 3.0

    image = Image.fromarray((np.clip(sky, 0, 1) ** (1 / 1.4) * 255).astype(np.uint8))
    image.save(OUT, quality=93)
    print("made", OUT)


if __name__ == "__main__":
    main()
