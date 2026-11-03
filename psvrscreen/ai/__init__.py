"""Local AI for your pictures and videos - everything runs on this PC, nothing is sent anywhere.

models   the model files (downloaded once to %LOCALAPPDATA%/PSVRScreen/models) and running them on the GPU
clip     what a picture shows / what words mean, as comparable vectors (search by talking, the galaxy)
faces    finding faces and telling whose they are
depth    how far every part of a picture is (2D -> 3D, stepping into a photo, living photos)
places   GPS position -> "Lisbon, Portugal" (offline, from a list of the world's cities)
index    all of that remembered per file (memory only in private mode)
ask      a spoken / typed question -> which pictures, and an answer
voice    listening for spoken commands (offline speech recognition)
"""
